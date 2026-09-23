"""Goldman Sachs careers (higher.gs.com).

The careers site searches through a GraphQL API,
``POST https://api-higher.gs.com/gateway/api/v1/graphql`` (operation
``GetRoles``), and sends the whole query with each request - seen in a
browser capture (2026-09-23) - so the same query can be sent here. Results
sort newest first and filter by country (the search's location filter,
e.g. "India").

Roles carry no posting date, so jobs are ``Precision.FIRST_SEEN``. Titles
read "Division-City-Corporate Title-Function" ("The Core
Engineering-Bengaluru-Associate-Software Engineering"); the corporate title
is Goldman's level (Analyst, Associate, Vice President).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import html_to_text
from .base import Provider, ProviderError, register

_API = "https://api-higher.gs.com/gateway/api/v1/graphql"
_ROLE_PAGE = "https://higher.gs.com/roles/{id}"
_PAGE_SIZE = 50
_MAX_PAGES = 12
_QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId corporateTitle jobTitle jobFunction
            locations { primary state country city } status division
            externalSource { sourceId } }
  }
}"""
#: The role page is a Next.js page; its data (with ``descriptionHtml``) is in __NEXT_DATA__.
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


@register("goldman_sachs")
class GoldmanSachsProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        country = config.get("country") or (hints.location or "").strip()
        filters = [{"filterCategoryType": "LOCATION", "filters": [{"filter": country, "subFilters": []}]}] if country else []
        company = config.get("company_name", "Goldman Sachs")

        jobs: dict[str, Job] = {}
        for page in range(_MAX_PAGES):
            body = {
                "operationName": "GetRoles",
                "query": _QUERY,
                "variables": {"searchQueryInput": {
                    "page": {"pageSize": _PAGE_SIZE, "pageNumber": page},
                    "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
                    "filters": filters,
                    "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
                    "searchTerm": config.get("search_term", ""),
                }},
            }
            payload = self._request_json("POST", config.get("url", _API), json=body)
            search = ((payload or {}).get("data") or {}).get("roleSearch") if isinstance(payload, dict) else None
            if not isinstance(search, dict):
                errors = (payload or {}).get("errors") if isinstance(payload, dict) else None
                raise ProviderError(f"goldman_sachs: no roleSearch in response ({str(errors)[:160]})")
            items = search.get("items") or []
            for item in items:
                job = self._to_job(item, company=company)
                jobs.setdefault(job.external_id, job)
            total = search.get("totalCount") or 0
            if not items or (page + 1) * _PAGE_SIZE >= total or len(jobs) >= hints.max_results:
                break
        return list(jobs.values())

    @staticmethod
    def _to_job(item: dict, *, company: str) -> Job:
        source_id = str(((item.get("externalSource") or {}).get("sourceId")) or item.get("roleId") or "")
        locations = tuple(
            ", ".join(p for p in (loc.get("city"), loc.get("state"), loc.get("country")) if p)
            for loc in item.get("locations") or [] if isinstance(loc, dict)
        )
        url = _ROLE_PAGE.format(id=source_id)
        return Job(
            company=company,
            title=(item.get("jobTitle") or "").strip(),
            url=url,
            source="goldman_sachs",
            external_id=source_id,
            precision=Precision.FIRST_SEEN,
            locations=tuple(loc for loc in locations if loc),
            department=item.get("jobFunction"),
            detail_url=url,
            raw=item,
        )

    def fetch_details(self, job: Job) -> Job:
        """The role page's ``descriptionHtml`` (from its __NEXT_DATA__), as text."""
        page = self._get_text(job.detail_url)
        match = _NEXT_DATA.search(page)
        if match:
            try:
                role = json.loads(match.group(1))["props"]["pageProps"]["role"]
                return replace(job, description=html_to_text(role.get("descriptionHtml")))
            except (ValueError, KeyError, TypeError):
                pass
        return replace(job, description=html_to_text(page))
