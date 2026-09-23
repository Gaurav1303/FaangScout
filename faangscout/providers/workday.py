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
Days Ago") - a day, not a time - so results are ``Precision.DATE_ONLY``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_relative
from .base import Provider, ProviderError, register

_PAGE_SIZE = 20
_DAY = timedelta(days=1)


def _posted_day(text: str) -> datetime | None:
    """ "Posted Today" -> midnight today (UTC); "Posted 3 Days Ago" -> 3 days back.

    Workday only says which day, so the timestamp is floored to midnight and
    marked DATE_ONLY - otherwise "Posted Today" reads as "posted <1h ago".
    """
    posted = parse_relative(text)
    if posted is None:
        return None
    return posted.replace(hour=0, minute=0, second=0, microsecond=0)


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

        # Neither Workday query is complete on its own - both seen live:
        # - A keyword search is relevance-ordered: NVIDIA's first page for
        #   "software engineer" was all 14-30+ days old (of 1,695), so its new
        #   postings sat beyond any page cap.
        # - The unfiltered list is newest-first on NVIDIA, but not reliably
        #   elsewhere: it missed today's "Software Engineer II" postings at
        #   Mastercard and a dozen at Salesforce that the keyword search found.
        # So run both and merge. The role filter decides what matches either way.
        passes = [("", True)]
        if hints.role_query:
            passes.insert(0, (hints.role_query, False))

        merged: dict[str, Job] = {}
        for search_text, stop_at_window in passes:
            for job in self._collect(url, search_text, hints, stop_at_window=stop_at_window,
                                     company=company, host=host, tenant=tenant, site=site):
                merged.setdefault(job.external_id or job.url, job)
        return list(merged.values())

    def _collect(self, url: str, search_text: str, hints: FetchHints, *, stop_at_window: bool,
                 company: str, host: str, tenant: str, site: str) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        stale_pages = 0
        while True:
            body = {"appliedFacets": {}, "limit": _PAGE_SIZE, "offset": offset, "searchText": search_text}
            payload = self._request_json("POST", url, json=body)
            if not isinstance(payload, dict):
                raise ProviderError(f"workday: {url} -> unexpected response (not a JSON object)")

            postings = payload.get("jobPostings", [])
            total = payload.get("total", offset + len(postings))
            page = [self._to_job(entry, company=company, host=host, tenant=tenant, site=site) for entry in postings]
            jobs.extend(page)
            offset += len(postings)
            if not postings or offset >= total or offset >= hints.max_results:
                break
            if stop_at_window and hints.since:
                # Loosely newest-first: stop after two consecutive pages that
                # are entirely older than the window (a day's grace, since
                # Workday dates are day-only), not at the first one.
                old = all(j.posted_at is not None and j.posted_at + _DAY < hints.since for j in page)
                stale_pages = stale_pages + 1 if old else 0
                if stale_pages >= 2:
                    break
        return jobs

    @staticmethod
    def _to_job(entry: dict, *, company: str, host: str, tenant: str, site: str) -> Job:
        # externalPath is site-relative ("/job/Pune-India/..."); the public
        # page lives under the site ("/CorporateCareers/job/Pune-India/...").
        path = entry.get("externalPath", "")
        url = f"https://{host}/{site.strip('/')}{path}" if path else f"https://{host}/{site.strip('/')}"
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
            posted_at=_posted_day(entry.get("postedOn", "")),
            precision=Precision.DATE_ONLY,
            locations=locations,
            remote=detect_remote(str(location)),
            detail_url=f"https://{host}/wday/cxs/{tenant}/{site.strip('/')}{path}" if path else "",
            raw=entry,
        )

    def fetch_details(self, job: Job) -> Job:
        """``GET .../wday/cxs/{tenant}/{site}{externalPath}`` -> ``jobPostingInfo.jobDescription``."""
        if not job.detail_url:
            return job
        payload = self._get_json(job.detail_url)
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            raise ProviderError(f"workday: {job.detail_url} has no jobPostingInfo")
        return replace(job, description=html_to_text(info.get("jobDescription")))
