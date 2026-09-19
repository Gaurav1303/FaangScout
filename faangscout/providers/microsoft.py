"""Microsoft's own career portal (jobs.careers.microsoft.com).

Like Amazon, Microsoft runs an in-house careers site rather than a third-party
ATS. The site is a single-page app backed by a JSON search service:

``https://gcsservices.careers.microsoft.com/search/api/v1/search``

The same caveat as ``amazon.py`` applies - this is the SPA's own endpoint, not
a supported public API, so it can change without notice. Parsing is defensive
and failures are explicit. ``postingDate`` is a full ISO timestamp, though
Microsoft commonly posts it at midnight UTC.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, parse_timestamp
from .base import Provider, ProviderError, register

_DEFAULT_BASE = "https://gcsservices.careers.microsoft.com"
_PATH = "/search/api/v1/search"
_JOB_URL = "https://jobs.careers.microsoft.com/global/en/job/{job_id}"
_PAGE_SIZE = 20


@register("microsoft")
class MicrosoftProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        base = config.get("base_url", _DEFAULT_BASE).rstrip("/")
        company = config.get("company_name", "Microsoft")

        params: dict[str, object] = {
            "l": "en_us",
            "pgSz": _PAGE_SIZE,
            "o": "Recent",
            "flt": "true",
            "pg": 1,
        }
        if hints.role_query:
            params["q"] = hints.role_query

        jobs: list[Job] = []
        page = 1
        while True:
            params["pg"] = page
            payload = self._get_json(base + _PATH, params=params)
            result = self._unwrap(payload)

            batch = result.get("jobs") or []
            jobs.extend(self._to_job(entry, company=company) for entry in batch)

            total = result.get("totalJobs", len(jobs))
            page += 1
            if len(batch) < _PAGE_SIZE or len(jobs) >= total or len(jobs) >= hints.max_results:
                break

        return jobs

    @staticmethod
    def _unwrap(payload: object) -> dict:
        """Dig ``operationResult.result`` out, failing loudly if it moved."""
        if not isinstance(payload, dict):
            raise ProviderError("microsoft: unexpected response (not a JSON object)")
        result = (payload.get("operationResult") or {}).get("result")
        if not isinstance(result, dict) or "jobs" not in result:
            raise ProviderError(
                "microsoft: response has no operationResult.result.jobs - "
                "the careers search API may have changed"
            )
        return result

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        props = entry.get("properties") or {}
        job_id = str(entry.get("jobId") or "")

        locations = props.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        primary = props.get("primaryLocation")
        if primary and primary not in locations:
            locations = [primary, *locations]

        flexibility = props.get("workSiteFlexibility") or ""

        return Job(
            company=company,
            title=entry.get("title", ""),
            url=_JOB_URL.format(job_id=job_id) if job_id else "",
            source="microsoft",
            external_id=job_id,
            posted_at=parse_timestamp(entry.get("postingDate")),
            precision=Precision.EXACT,
            locations=tuple(str(loc) for loc in locations if loc),
            remote=detect_remote(flexibility, " ".join(str(loc) for loc in locations)),
            department=props.get("profession") or props.get("discipline"),
            employment_type=props.get("employmentType"),
            description=props.get("description", "") or "",
            raw=entry,
        )
