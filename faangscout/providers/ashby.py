"""Ashby job board API.

Public POST JSON-RPC-flavoured endpoint used by the embeddable job board
widget: ``POST https://api.ashbyhq.com/posting-api/job-board/{board}``. No
auth required for a public board. Ashby exposes ``publishedAt`` (exact).
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, parse_timestamp, strip_html
from .base import Provider, ProviderError, register

_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


@register("ashby")
class AshbyProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        board = config.get("board") or config.get("token")
        if not board:
            raise ProviderError("ashby: config requires 'board'")

        try:
            response = self._client.get(
                _URL.format(board=board), params={"includeCompensation": "false"}
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - normalised into ProviderError
            raise ProviderError(f"ashby: board {board!r} -> {exc!r}") from exc

        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ProviderError(f"ashby: unexpected response shape for board {board!r}")

        company = config.get("company_name", board)
        return [self._to_job(entry, company=company) for entry in payload["jobs"]]

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        location = (entry.get("location") or "").strip()
        description = strip_html(entry.get("descriptionPlain") or entry.get("description", ""))
        is_remote = entry.get("isRemote")

        return Job(
            company=company,
            title=entry.get("title", ""),
            url=entry.get("jobUrl") or entry.get("applyUrl", ""),
            source="ashby",
            external_id=str(entry.get("id", "")),
            posted_at=parse_timestamp(entry.get("publishedAt")),
            precision=Precision.EXACT,
            locations=tuple(loc for loc in [location] if loc),
            remote=bool(is_remote) if is_remote is not None else detect_remote(location, description),
            department=entry.get("department") or entry.get("team"),
            employment_type=entry.get("employmentType"),
            description=description,
            raw=entry,
        )
