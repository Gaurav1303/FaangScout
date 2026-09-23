"""Provider protocol: one class per ATS/career-site backend.

A provider's only job is: given config for one company's board, return a list
of ``Job`` objects. Everything else (filtering, deduping, time windows) lives
outside. Adding a new job board means adding one file here and registering it
in ``REGISTRY`` - nothing else in the codebase needs to change.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import httpx

from ..models import FetchHints, Job

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
USER_AGENT = "FaangScout/0.1 (+https://github.com/gaurav1303/faangscout)"


class ProviderError(RuntimeError):
    """Raised when a provider cannot fetch or parse a board's postings."""


class Provider(ABC):
    """Base class for a job-board backend.

    Subclasses implement :meth:`fetch`. Both sync and async entry points are
    provided; :meth:`fetch` is sync by default (most boards are simple REST/
    JSON and the orchestrator runs providers in a thread pool), but a provider
    that benefits from native async can override :meth:`afetch` instead.
    """

    name: str = "base"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Provider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @abstractmethod
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        """Return every job posting for one board.

        ``config`` is whatever this provider needs to identify the board
        (e.g. ``{"board": "stripe"}`` for Greenhouse). ``hints`` are advisory
        narrowing the orchestrator would like but the provider may ignore.
        Raise :class:`ProviderError` on failure - never return a partial list
        silently, since a truncated result would just look like "no new jobs".
        """
        raise NotImplementedError

    #: How many detail requests to run at once against this provider.
    detail_concurrency: int = 4

    def fetch_details(self, job: Job) -> Job:
        """Return ``job`` with ``description`` filled in from ``job.detail_url``.

        Only called for jobs whose listing had no description, and only once
        they have passed the cheaper filters. Providers whose listings already
        carry descriptions keep this no-op default.
        """
        return job

    async def afetch(self, config: dict, hints: FetchHints) -> list[Job]:
        """Default async wrapper: runs the sync :meth:`fetch` in a thread."""
        import asyncio

        return await asyncio.to_thread(self.fetch, config, hints)

    def _get_json(self, url: str, **kwargs) -> object:
        return self._request_json("GET", url, **kwargs)

    def _request_json(self, method: str, url: str, **kwargs) -> object:
        """Send a request and parse JSON, retrying transient failures.

        Retries rate limiting (429), gateway errors (502/503/504) and timeouts
        a couple of times with backoff, honouring ``Retry-After``. Without
        this, one 429 - seen live from Qualcomm - drops a whole company from
        that day's report.
        """
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                if last:
                    raise ProviderError(f"{self.name}: {url} -> {exc!r}") from exc
                self._sleep(RETRY_BACKOFF[attempt])
                continue
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: {url} -> {exc!r}") from exc

            if response.status_code in RETRY_STATUSES and not last:
                self._sleep(_retry_delay(response, attempt))
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProviderError(f"{self.name}: {url} -> HTTP {exc.response.status_code}") from exc
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError(f"{self.name}: {url} -> invalid JSON") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    #: Indirection so tests can retry without actually waiting.
    _sleep = staticmethod(time.sleep)


RETRY_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_BACKOFF = (2.0, 6.0)
_MAX_RETRY_AFTER = 30.0


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After", "")
    try:
        return min(float(header), _MAX_RETRY_AFTER)
    except ValueError:
        return RETRY_BACKOFF[attempt]


REGISTRY: dict[str, type[Provider]] = {}


def register(name: str):
    """Class decorator: ``@register("greenhouse")`` adds a provider to REGISTRY."""

    def decorator(cls: type[Provider]) -> type[Provider]:
        cls.name = name
        REGISTRY[name] = cls
        return cls

    return decorator


def get_provider(name: str, client: httpx.Client | None = None) -> Provider:
    try:
        cls = REGISTRY[name]
    except KeyError as exc:
        raise ProviderError(f"unknown provider: {name!r} (known: {sorted(REGISTRY)})") from exc
    return cls(client=client)
