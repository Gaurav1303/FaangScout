"""Greenhouse job board API.

Public, unauthenticated JSON endpoint:
``https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true``

``{board}`` is the token in a company's Greenhouse URL, e.g. for
``https://job-boards.greenhouse.io/stripe`` the token is ``stripe``.
Postings carry ``updated_at``, which bulk edits refresh, and on boards that
send it, ``first_published``. The latter is preferred (``Precision.EXACT``);
``updated_at`` is the ``Precision.APPROXIMATE`` fallback.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text
from .base import Provider, ProviderError, register

_DEFAULT_BASE = "https://boards-api.greenhouse.io"
_PATH = "/v1/boards/{board}/jobs"


@register("greenhouse")
class GreenhouseProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        board = config.get("board") or config.get("token")
        if not board:
            raise ProviderError("greenhouse: config requires 'board'")

        base = config.get("base_url", _DEFAULT_BASE).rstrip("/")
        payload = self._get_json(base + _PATH.format(board=board), params={"content": "true"})
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ProviderError(f"greenhouse: unexpected response shape for board {board!r}")

        company = config.get("company_name", board)
        jobs: list[Job] = []
        for entry in payload["jobs"]:
            jobs.append(self._to_job(entry, board=board, company=company))
        return jobs

    @staticmethod
    def _to_job(entry: dict, *, board: str, company: str) -> Job:
        from ..normalize import parse_timestamp

        title = entry.get("title", "")
        description = html_to_text(entry.get("content", ""))
        location = ((entry.get("location") or {}).get("name") or "").strip()
        locations = tuple(loc for loc in [location] if loc)
        departments = entry.get("departments") or []
        department = departments[0].get("name") if departments else None

        # updated_at moves whenever a recruiter edits a posting, and boards
        # bulk-edit: one live run saw 45 months-old Compass jobs all "updated
        # 9h ago". A first-publish date, when the board sends one, is the real
        # posting time.
        published = parse_timestamp(entry.get("first_published"))
        posted_at = published or parse_timestamp(entry.get("updated_at"))
        precision = Precision.EXACT if published else Precision.APPROXIMATE

        return Job(
            company=company,
            title=title,
            url=entry.get("absolute_url", f"https://job-boards.greenhouse.io/{board}"),
            source="greenhouse",
            external_id=str(entry.get("id", "")),
            posted_at=posted_at,
            precision=precision,
            locations=locations,
            remote=detect_remote(location, description),
            department=department,
            description=description,
            raw=entry,
        )
