"""Atlassian's careers listing (atlassian.com/company/careers).

The careers page loads every open job in one response,
``GET https://www.atlassian.com/endpoint/careers/listings`` - seen in a
browser capture (2026-09-23), 292 jobs. Each carries its full text
(``overview``, ``responsibilities``, ``qualifications``), locations, a
category ("Engineering", "Sales", ...), and the iCIMS posting it mirrors
(``portalJobPost.portalUrl``, ``updatedDate``).

``updatedDate`` ("2026-09-22 12:42 AM") is when the posting last changed,
not when it opened, so jobs are ``Precision.APPROXIMATE``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_API = "https://www.atlassian.com/endpoint/careers/listings"


def _updated(value: object) -> datetime | None:
    """ "2026-09-22 12:42 AM" -> aware UTC (the site doesn't say which zone)."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %I:%M %p").replace(tzinfo=UTC)
        except ValueError:
            pass
    return parse_timestamp(value)


@register("atlassian")
class AtlassianProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        payload = self._get_json(config.get("url", _API))
        if not isinstance(payload, list):
            raise ProviderError("atlassian: listings response is not a list")
        company = config.get("company_name", "Atlassian")
        return [self._to_job(entry, company=company) for entry in payload if isinstance(entry, dict)]

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        portal = entry.get("portalJobPost") or {}
        locations = tuple(str(loc).strip() for loc in entry.get("locations") or [] if str(loc).strip())
        job_id = entry.get("id") or portal.get("id")
        text = "\n\n".join(
            html_to_text(entry.get(key)) for key in ("overview", "responsibilities", "qualifications") if entry.get(key)
        )
        return Job(
            company=company,
            title=(entry.get("title") or "").strip(),
            url=portal.get("portalUrl") or entry.get("applyUrl") or "",
            source="atlassian",
            external_id=str(job_id or ""),
            posted_at=_updated(portal.get("updatedDate")),
            precision=Precision.APPROXIMATE,
            locations=locations,
            remote=detect_remote(" ".join(locations)),
            department=entry.get("category"),
            description=text,
            raw={k: v for k, v in entry.items() if k not in ("overview", "responsibilities", "qualifications")},
        )
