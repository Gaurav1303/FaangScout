"""SmartRecruiters public postings API.

``https://api.smartrecruiters.com/v1/companies/{company}/postings``, paginated
via ``offset``/``limit``. Exposes ``releasedDate`` (exact).
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_DEFAULT_BASE = "https://api.smartrecruiters.com"
_PATH = "/v1/companies/{company}/postings"
_PAGE_SIZE = 100


@register("smartrecruiters")
class SmartRecruitersProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        company_token = config.get("company") or config.get("token")
        if not company_token:
            raise ProviderError("smartrecruiters: config requires 'company'")

        company = config.get("company_name", company_token)
        base = config.get("base_url", _DEFAULT_BASE).rstrip("/")
        jobs: list[Job] = []
        offset = 0
        while True:
            payload = self._get_json(
                base + _PATH.format(company=company_token),
                params={"limit": _PAGE_SIZE, "offset": offset},
            )
            if not isinstance(payload, dict):
                raise ProviderError(f"smartrecruiters: unexpected response for {company_token!r}")
            batch = payload.get("content", [])
            jobs.extend(self._to_job(entry, company=company) for entry in batch)
            offset += len(batch)
            total = payload.get("totalFound", offset)
            if len(batch) < _PAGE_SIZE or offset >= total or offset >= hints.max_results:
                break
        return jobs

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        location_obj = entry.get("location") or {}
        location = ", ".join(
            filter(None, [location_obj.get("city"), location_obj.get("region"), location_obj.get("country")])
        )
        is_remote = location_obj.get("remote")

        return Job(
            company=company,
            title=entry.get("name", ""),
            url=(entry.get("ref") or {}).get("jobAdUrl", "") or entry.get("applyUrl", ""),
            source="smartrecruiters",
            external_id=str(entry.get("id", "")),
            posted_at=parse_timestamp(entry.get("releasedDate")),
            precision=Precision.EXACT,
            locations=tuple(loc for loc in [location] if loc),
            remote=bool(is_remote) if is_remote is not None else detect_remote(location),
            department=(entry.get("department") or {}).get("label"),
            description=html_to_text(entry.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "")),
            raw=entry,
        )
