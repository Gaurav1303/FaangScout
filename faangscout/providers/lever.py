"""Lever job board API.

Public JSON endpoint: ``https://api.lever.co/v0/postings/{site}?mode=json``.
``{site}`` is the token in ``https://jobs.lever.co/{site}``. Lever exposes a
true ``createdAt`` epoch-millisecond timestamp, so this is ``Precision.EXACT``.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_DEFAULT_BASE = "https://api.lever.co"
_PATH = "/v0/postings/{site}"


@register("lever")
class LeverProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        site = config.get("site") or config.get("token")
        if not site:
            raise ProviderError("lever: config requires 'site'")

        base = config.get("base_url", _DEFAULT_BASE).rstrip("/")
        payload = self._get_json(base + _PATH.format(site=site), params={"mode": "json"})
        if not isinstance(payload, list):
            raise ProviderError(f"lever: unexpected response shape for site {site!r}")

        company = config.get("company_name", site)
        return [self._to_job(entry, company=company) for entry in payload]

    @staticmethod
    def _to_job(entry: dict, *, company: str) -> Job:
        categories = entry.get("categories") or {}
        location = (categories.get("location") or "").strip()
        commitment = categories.get("commitment")
        # Lever keeps requirements in "lists" ({text: heading, content: <li>...}).
        parts = [entry.get("descriptionPlain") or html_to_text(entry.get("description", ""))]
        for block in entry.get("lists") or []:
            if isinstance(block, dict):
                parts.append(f"{block.get('text', '')}:\n{html_to_text(block.get('content', ''))}")
        parts.append(entry.get("additionalPlain") or "")
        description = "\n".join(p for p in parts if p)

        return Job(
            company=company,
            title=entry.get("text", ""),
            url=entry.get("hostedUrl") or entry.get("applyUrl", ""),
            source="lever",
            external_id=str(entry.get("id", "")),
            posted_at=parse_timestamp(entry.get("createdAt")),
            precision=Precision.EXACT,
            locations=tuple(loc for loc in [location] if loc),
            remote=detect_remote(location, entry.get("workplaceType", ""), description),
            department=categories.get("team") or categories.get("department"),
            employment_type=commitment,
            description=description,
            raw=entry,
        )
