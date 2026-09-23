"""Amazon's own career portal (amazon.jobs).

Amazon does not publish postings through a third-party ATS - amazon.jobs is
an in-house site. Its search page is backed by a JSON endpoint that the site's
own frontend calls:

``https://www.amazon.jobs/en/search.json?base_query=...&sort=recent``

This is an internal endpoint rather than a documented, supported public API:
it carries no stability guarantee and Amazon can change or gate it without
notice. Parsing here is defensive for that reason, and a shape change surfaces
as a :class:`ProviderError` naming what was missing rather than as an empty
result that reads like "no jobs today". Run ``faangscout --check`` to confirm
the endpoint still answers.

``posted_date`` is a calendar date with no time of day ("March 5, 2026"), so
postings are :attr:`Precision.DATE_ONLY`.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_DEFAULT_BASE = "https://www.amazon.jobs"
_PATH = "/en/search.json"
_PAGE_SIZE = 100


@register("amazon")
class AmazonProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        base = config.get("base_url", _DEFAULT_BASE).rstrip("/")
        company = config.get("company_name", "Amazon")

        params: dict[str, object] = {
            "result_limit": _PAGE_SIZE,
            "sort": "recent",
            "offset": 0,
        }
        # The board can narrow server-side; the filter pipeline still decides.
        if hints.role_query:
            params["base_query"] = hints.role_query
        for key in ("country", "normalized_country_code", "category"):
            if key in config:
                params[key] = config[key]

        jobs: list[Job] = []
        offset = 0
        while True:
            params["offset"] = offset
            payload = self._get_json(base + _PATH, params=params)
            if not isinstance(payload, dict):
                raise ProviderError("amazon: unexpected response (not a JSON object)")
            if payload.get("error"):
                raise ProviderError(f"amazon: API returned error {payload['error']!r}")
            if "jobs" not in payload:
                raise ProviderError(
                    f"amazon: response has no 'jobs' key (got {sorted(payload)[:6]}) - "
                    "the amazon.jobs endpoint may have changed"
                )

            batch = payload["jobs"] or []
            jobs.extend(self._to_job(entry, company=company, base=base) for entry in batch)
            offset += len(batch)
            total = payload.get("hits", offset)
            if len(batch) < _PAGE_SIZE or offset >= total or offset >= hints.max_results:
                break

        return jobs

    @staticmethod
    def _to_job(entry: dict, *, company: str, base: str) -> Job:
        path = entry.get("job_path", "")
        location = entry.get("normalized_location") or entry.get("location") or ""
        # Requirements live in separate list fields. Keep them as headed
        # sections so the experience filter can tell basic from preferred.
        sections = [
            ("Basic qualifications", entry.get("basic_qualifications")),
            ("Description", entry.get("description")),
            ("Preferred qualifications", entry.get("preferred_qualifications")),
        ]
        description = "\n".join(
            f"{heading}:\n{html_to_text(body)}" for heading, body in sections if body
        )

        return Job(
            company=company,
            title=entry.get("title", ""),
            url=f"{base}{path}" if path else base,
            source="amazon",
            external_id=str(entry.get("id_icims") or entry.get("id") or ""),
            posted_at=parse_timestamp(entry.get("posted_date")),
            precision=Precision.DATE_ONLY,
            locations=tuple(loc for loc in [location] if loc),
            remote=detect_remote(location, description),
            department=entry.get("job_category") or entry.get("business_category"),
            employment_type=entry.get("job_schedule_type"),
            description=description,
            raw=entry,
        )
