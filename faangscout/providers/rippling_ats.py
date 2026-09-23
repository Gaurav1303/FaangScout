"""Rippling's applicant-tracking boards (ats.rippling.com).

``GET https://ats.rippling.com/api/v2/board/{board}/jobs?page=N&pageSize=M``
returns ``{items, page, pageSize, totalItems, totalPages}`` - found behind
Rippling's own careers page (2026-09-23), where it listed 609 postings. The
same posting can appear once per location, so items are merged by id.

Postings carry no date, so these jobs are ``Precision.FIRST_SEEN``: dated by
when a run first saw them (see ``first_seen.py``).
"""

from __future__ import annotations

from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import html_to_text
from .base import Provider, ProviderError, register

_API = "https://ats.rippling.com/api/v2/board/{board}/jobs"
_PAGE_SIZE = 100
_MAX_PAGES = 30


def _location(loc: dict) -> str:
    name = (loc.get("name") or "").strip()
    country = (loc.get("country") or "").strip()
    if country and country.lower() not in name.lower():
        return f"{name}, {country}" if name else country
    return name


@register("rippling_ats")
class RipplingAtsProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        board = config.get("board")
        if not board:
            raise ProviderError("rippling_ats: config requires 'board'")
        company = config.get("company_name", board)
        url = config.get("url", _API.format(board=board))

        merged: dict[str, dict] = {}
        for page in range(_MAX_PAGES):
            payload = self._get_json(url, params={"page": page, "pageSize": _PAGE_SIZE})
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ProviderError(f"rippling_ats: {board} response has no 'items' list")
            for item in items:
                entry = merged.setdefault(item.get("id"), {**item, "locations": []})
                entry["locations"] = entry["locations"] + list(item.get("locations") or [])
            total_pages = payload.get("totalPages")
            if not items or (isinstance(total_pages, int) and page + 1 >= total_pages):
                break
            if len(merged) >= hints.max_results * 3:
                break
        return [self._to_job(item, company=company) for item in merged.values()]

    @staticmethod
    def _to_job(item: dict, *, company: str) -> Job:
        locations = tuple(dict.fromkeys(_location(loc) for loc in item.get("locations") or [] if isinstance(loc, dict)))
        workplace = {(loc.get("workplaceType") or "") for loc in item.get("locations") or [] if isinstance(loc, dict)}
        return Job(
            company=company,
            title=item.get("name", ""),
            url=item.get("url", ""),
            source="rippling_ats",
            external_id=str(item.get("id") or ""),
            precision=Precision.FIRST_SEEN,
            locations=tuple(loc for loc in locations if loc),
            remote=True if workplace == {"REMOTE"} else (False if "ON_SITE" in workplace else None),
            department=(item.get("department") or {}).get("name"),
            detail_url=item.get("url", ""),
            raw=item,
        )

    def fetch_details(self, job: Job) -> Job:
        """The posting's public page, as text."""
        if not job.detail_url:
            return job
        return replace(job, description=html_to_text(self._get_text(job.detail_url)))
