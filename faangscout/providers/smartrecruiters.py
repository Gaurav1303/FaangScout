"""SmartRecruiters public postings API.

``https://api.smartrecruiters.com/v1/companies/{company}/postings``, paginated
via ``offset``/``limit``. Exposes ``releasedDate`` (exact).

A listing's ``ref`` is the posting's API URL (a string), which returns the
full ad (``jobAd.sections``) - read only for jobs that pass the cheaper
filters. The public page is ``jobs.smartrecruiters.com/{company}/{id}``.
"""

from __future__ import annotations

from dataclasses import replace

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
            jobs.extend(self._to_job(entry, company=company, token=company_token) for entry in batch)
            offset += len(batch)
            total = payload.get("totalFound", offset)
            if len(batch) < _PAGE_SIZE or offset >= total or offset >= hints.max_results:
                break
        return jobs

    @staticmethod
    def _to_job(entry: dict, *, company: str, token: str = "") -> Job:
        location_obj = entry.get("location") or {}
        location = ", ".join(
            filter(None, [location_obj.get("city"), location_obj.get("region"), location_obj.get("country")])
        )
        is_remote = location_obj.get("remote")

        ref = entry.get("ref")
        identifier = (entry.get("company") or {}).get("identifier") or token
        posting_id = entry.get("id")
        public = f"https://jobs.smartrecruiters.com/{identifier}/{posting_id}" if identifier and posting_id else ""
        return Job(
            company=company,
            title=entry.get("name", ""),
            url=public or entry.get("applyUrl", "") or (ref if isinstance(ref, str) else ""),
            source="smartrecruiters",
            external_id=str(entry.get("id", "")),
            posted_at=parse_timestamp(entry.get("releasedDate")),
            precision=Precision.EXACT,
            locations=tuple(loc for loc in [location] if loc),
            remote=bool(is_remote) if is_remote is not None else detect_remote(location),
            department=(entry.get("department") or {}).get("label"),
            description=_ad_text(entry),
            detail_url=ref if isinstance(ref, str) else "",
            raw=entry,
        )

    def fetch_details(self, job: Job) -> Job:
        """``GET {ref}`` -> the full ad's sections, as text."""
        if not job.detail_url:
            return job
        payload = self._get_json(job.detail_url)
        return replace(job, description=_ad_text(payload if isinstance(payload, dict) else {}))


def _ad_text(entry: dict) -> str:
    sections = ((entry.get("jobAd") or {}).get("sections") or {})
    parts = [html_to_text((sections.get(key) or {}).get("text", ""))
             for key in ("jobDescription", "qualifications", "additionalInformation")]
    return "\n\n".join(p for p in parts if p)
