"""Provider protocol: one class per ATS/career-site backend.

A provider's only job is: given config for one company's board, return a list
of ``Job`` objects. Everything else (filtering, deduping, time windows) lives
outside. Adding a new job board means adding one file here and registering it
in ``REGISTRY`` - nothing else in the codebase needs to change.
"""

from __future__ import annotations

import logging
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

    async def afetch(self, config: dict, hints: FetchHints) -> list[Job]:
        """Default async wrapper: runs the sync :meth:`fetch` in a thread."""
        import asyncio

        return await asyncio.to_thread(self.fetch, config, hints)

    def _get_json(self, url: str, **kwargs) -> object:
        try:
            response = self._client.get(url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name}: {url} -> HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {url} -> {exc!r}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name}: {url} -> invalid JSON") from exc


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
