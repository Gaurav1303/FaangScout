"""ShareChat's careers API (sharechat.com/api/careersList).

Found behind sharechat.com/careers (2026-09-23): postings grouped by team
under ``data.careersList[].data[]``, each with explicit ``yrsOfExpMin`` /
``yrsOfExpMax``, office locations, and ``approvedDate`` (epoch ms). The
experience range is written into the description so the experience filter
reads it like any stated requirement. No per-posting link is exposed, so
jobs link the careers page.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_API = "https://sharechat.com/api/careersList"
_CAREERS = "https://sharechat.com/careers"


@register("sharechat")
class ShareChatProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        payload = self._get_json(config.get("url", _API), params={"limit": 100})
        groups = ((payload or {}).get("data") or {}).get("careersList") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            raise ProviderError("sharechat: response has no data.careersList")
        company = config.get("company_name", "ShareChat")
        return [
            self._to_job(entry, company=company, team=group.get("title"))
            for group in groups if isinstance(group, dict)
            for entry in group.get("data") or [] if isinstance(entry, dict)
        ]

    @staticmethod
    def _to_job(entry: dict, *, company: str, team: str | None) -> Job:
        lo, hi = entry.get("yrsOfExpMin"), entry.get("yrsOfExpMax")
        stated = f"Experience required: {lo}-{hi} years" if lo is not None and hi is not None else ""
        body = html_to_text(entry.get("jobDescription"))
        return Job(
            company=company,
            title=entry.get("requisitionTitle") or entry.get("designation") or "",
            url=_CAREERS,
            source="sharechat",
            external_id=str(entry.get("requisitionId") or ""),
            posted_at=parse_timestamp(entry.get("approvedDate") or entry.get("createdDate")),
            precision=Precision.EXACT,
            locations=tuple(entry.get("officeLocationNames") or ()),
            department=entry.get("orgUnitName") or team,
            employment_type=entry.get("employmentType"),
            description="\n".join(p for p in (stated, body) if p),
            raw=entry,
        )
