"""Workday CXS (career site) API.

Workday tenants serve postings from a POST JSON endpoint:
``https://{host}/wday/cxs/{tenant}/{site}/jobs``

``host`` includes the numbered subdomain Workday assigns per tenant (e.g.
``amazon.wd1.myworkdayjobs.com``) - there is no way to derive that number from
the company name, so ``host`` must be supplied in the board config (see
``companies/data``). ``tenant`` and ``site`` are the two path segments visible
in the public careers URL, e.g. for
``https://amazon.wd1.myworkdayjobs.com/en-US/Amazon`` tenant is ``amazon`` and
site is ``en-US/Amazon`` (or just ``Amazon`` - Workday is lenient here).

Workday exposes ``postedOn`` as relative prose ("Posted Today", "Posted 30+
Days Ago"), so results are ``Precision.APPROXIMATE`` and anything past the
"30+" cutoff has no usable date at all.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, parse_relative
from .base import Provider, ProviderError, register

_PAGE_SIZE = 20


@register("workday")
class WorkdayProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        host = config.get("host")
        tenant = config.get("tenant")
        site = config.get("site")
        if not (host and tenant and site):
            raise ProviderError("workday: config requires 'host', 'tenant', and 'site'")

        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        company = config.get("company_name", tenant)

        jobs: list[Job] = []
        offset = 0
        while True:
            body = {
                "appliedFacets": {},
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": hints.role_query or "",
            }
            try:
                response = self._client.post(url, json=body)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"workday: {tenant}/{site} -> {exc!r}") from exc

            postings = payload.get("jobPostings", [])
            total = payload.get("total", offset + len(postings))
            jobs.extend(self._to_job(entry, company=company, host=host) for entry in postings)
            offset += len(postings)
            if not postings or offset >= total or offset >= hints.max_results:
                break

        return jobs

    @staticmethod
    def _to_job(entry: dict, *, company: str, host: str) -> Job:
        path = entry.get("externalPath", "")
        url = f"https://{host}{path}" if path else ""
        location = entry.get("locationsText") or entry.get("locations", "")

        # locationsText is a single free-form string ("Seattle, WA" or
        # "Seattle, WA | Remote - USA"); the comma is part of one location
        # (city, state), so only " | " (Workday's multi-location separator)
        # splits it into several.
        locations = tuple(loc.strip() for loc in str(location).split("|") if loc.strip())

        return Job(
            company=company,
            title=entry.get("title", ""),
            url=url,
            source="workday",
            external_id=entry.get("bulletFields", [None])[0] or path,
            posted_at=parse_relative(entry.get("postedOn", "")),
            precision=Precision.APPROXIMATE,
            locations=locations,
            remote=detect_remote(str(location)),
            raw=entry,
        )
