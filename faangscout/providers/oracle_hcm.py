"""Oracle Recruiting Cloud "Candidate Experience" sites (JPMorgan Chase, ...).

The careers site at
``https://{host}/hcmUI/CandidateExperience/en/sites/{site}/requisitions`` is a
single-page app over Oracle's HCM REST resource:

``https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions``

queried with a ``finder`` expression naming the site, a page window, a sort,
and a keyword. JPMorgan's site (``jpmc.fa.oraclecloud.com``, ``CX_1001``) was
identified from its careers page on 2026-09-23.

``PostedDate`` is a calendar date, so postings are ``Precision.DATE_ONLY``.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, parse_timestamp
from .base import Provider, ProviderError, register

_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_PAGE_SIZE = 25
_MAX_PAGES = 20


def build_finder(site: str, *, offset: int, keyword: str | None) -> str:
    parts = [
        f"siteNumber={site}",
        "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS",
        f"limit={_PAGE_SIZE}",
        f"offset={offset}",
        "sortBy=POSTING_DATES_DESC",
    ]
    if keyword:
        parts.append(f'keyword="{keyword}"')
    return "findReqs;" + ",".join(parts)


@register("oracle_hcm")
class OracleHcmProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        host = config.get("host")
        site = config.get("site")
        if not (host and site):
            raise ProviderError("oracle_hcm: config requires 'host' and 'site'")

        base = config.get("base_url", f"https://{host}").rstrip("/")
        company = config.get("company_name", host)
        job_base = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job"

        jobs: list[Job] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            payload = self._get_json(
                base + _PATH,
                params={
                    "onlyData": "true",
                    "expand": "requisitionList.secondaryLocations",
                    "finder": build_finder(site, offset=offset, keyword=hints.role_query),
                },
            )
            result = self._unwrap(payload, host)
            batch = result.get("requisitionList") or []
            page = [self._to_job(r, company=company, job_base=job_base) for r in batch]
            jobs.extend(page)
            offset += len(batch)

            total = result.get("TotalJobsCount")
            if not batch or len(batch) < _PAGE_SIZE or len(jobs) >= hints.max_results:
                break
            if isinstance(total, int) and offset >= total:
                break
            if hints.since and all(j.posted_at and j.posted_at < hints.since for j in page):
                break
        return jobs

    @staticmethod
    def _unwrap(payload: object, host: str) -> dict:
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ProviderError(f"oracle_hcm: {host} response has no 'items' list")
        if not items:
            return {}
        if not isinstance(items[0], dict) or "requisitionList" not in items[0]:
            raise ProviderError(f"oracle_hcm: {host} items[0] has no requisitionList")
        return items[0]

    @staticmethod
    def _to_job(entry: dict, *, company: str, job_base: str) -> Job:
        job_id = str(entry.get("Id") or "")
        locations = [entry.get("PrimaryLocation")]
        for extra in entry.get("secondaryLocations") or []:
            if isinstance(extra, dict):
                locations.append(extra.get("Name"))
        locations = [loc for loc in locations if loc]
        workplace = entry.get("WorkplaceType") or ""

        return Job(
            company=company,
            title=entry.get("Title", ""),
            url=f"{job_base}/{job_id}" if job_id else job_base,
            source="oracle_hcm",
            external_id=job_id,
            posted_at=parse_timestamp(entry.get("PostedDate")),
            precision=Precision.DATE_ONLY,
            locations=tuple(dict.fromkeys(locations)),
            remote=detect_remote(workplace, " ".join(locations)),
            department=entry.get("JobFamily") or entry.get("Organization"),
            description=entry.get("ShortDescriptionStr") or "",
            raw=entry,
        )
