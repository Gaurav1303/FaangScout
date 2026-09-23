"""PhonePe's own job feed.

PhonePe's careers page loads its list from
``https://www.phonepe.com/apollo/job-postings/latest.json`` - found by loading
the page in a browser (2026-09-23). The feed also carries postings that
aren't open to the public (status NOT_PUBLISHED, PRIVATE, INTERNAL - e.g.
"Test Job IJP"); only ``PUBLIC`` ones are returned. Each public posting links
its SmartRecruiters page (``applyUrl``), which is also where the full
description is read from.

``updatedAt`` is a .NET date at midnight: a day, so ``Precision.DATE_ONLY``.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_FEED = "https://www.phonepe.com/apollo/job-postings/latest.json"
_CAREERS = "https://www.phonepe.com/careers/job-openings/"


@register("phonepe_feed")
class PhonePeFeedProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        payload = self._get_json(config.get("url", _FEED))
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ProviderError("phonepe_feed: response has no 'results' list")
        company = config.get("company_name", "PhonePe")
        return [self._to_job(e, company=company) for e in results if isinstance(e, dict) and e.get("status") == "PUBLIC"]

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        apply_url = entry.get("applyUrl") or ""
        location = (entry.get("location") or "").strip()
        return Job(
            company=company,
            title=entry.get("title", ""),
            url=apply_url or _CAREERS,
            source="phonepe_feed",
            external_id=apply_url or f"{entry.get('title')}|{location}",
            posted_at=parse_timestamp(entry.get("updatedAt")),
            precision=Precision.DATE_ONLY,
            locations=(location,) if location else (),
            remote=detect_remote(location),
            department=entry.get("department"),
            employment_type=entry.get("type"),
            detail_url=apply_url,
            raw=entry,
        )

    def fetch_details(self, job: Job) -> Job:
        """The SmartRecruiters posting page, as text."""
        if not job.detail_url:
            return job
        return replace(job, description=html_to_text(self._get_text(job.detail_url)))
