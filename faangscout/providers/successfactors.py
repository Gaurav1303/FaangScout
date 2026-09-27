"""SAP SuccessFactors career sites (Recruiting Marketing, "RMK") - PayU.

The site's own search page is server-rendered and sortable by date:
``GET https://{host}/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow=N``
lists 25 postings per page as table rows with a ``jobTitle-link``
(``/{Brand}/job/{City}-{Slug}/{id}/``), a ``jobLocation`` and a
``jobDate`` - seen on careers.payu.in (2026-09-27), 135 postings over 6
pages. Newest first, so paging stops once a whole page predates the window.

``jobDate`` is a calendar day, so jobs are ``Precision.DATE_ONLY``. The full
ad is the job page itself (``span.jobdescription``).
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_ROW = re.compile(r'<tr class="data-row[^"]*"[^>]*>(?P<row>.*?)</tr>', re.S)
_TITLE = re.compile(r'<a href="(?P<href>/[^"]+/job/[^"]+?/(?P<id>\d+)/)"[^>]*class="jobTitle-link"[^>]*>(?P<title>.*?)</a>', re.S)
_LOCATION = re.compile(r'<span class="jobLocation">(.*?)</span>', re.S)
_DATE = re.compile(r'<span class="jobDate[^"]*">(.*?)</span>', re.S)
_TOTAL = re.compile(r"of\s*<b>(\d+)</b>")
_DESCRIPTION = re.compile(r'<span class="jobdescription">(.*?)</span>\s*</div>', re.S)
_PAGE_SIZE = 25
_MAX_PAGES = 20
_DAY = timedelta(days=1)


@register("successfactors")
class SuccessFactorsProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        host = config.get("host")
        if not host:
            raise ProviderError("successfactors: config requires 'host'")
        base = config.get("base_url", f"https://{host}").rstrip("/")
        company = config.get("company_name", host)

        jobs: dict[str, Job] = {}
        total = None
        for page in range(_MAX_PAGES):
            html = self._get_text(f"{base}/search/", params={
                "q": "", "sortColumn": "referencedate", "sortDirection": "desc", "startrow": page * _PAGE_SIZE})
            rows = [self._to_job(m.group("row"), company=company, base=base) for m in _ROW.finditer(html)]
            rows = [r for r in rows if r is not None]
            if page == 0:
                if not rows and "jobTitle-link" not in html:
                    raise ProviderError(f"successfactors: {host} search page has no job rows (layout changed?)")
                m = _TOTAL.search(html)
                total = int(m.group(1)) if m else None
            for job in rows:
                jobs.setdefault(job.external_id, job)
            if not rows or (total is not None and (page + 1) * _PAGE_SIZE >= total) or len(jobs) >= hints.max_results:
                break
            if hints.since and all(j.posted_at and j.posted_at + _DAY < hints.since for j in rows):
                break
        return list(jobs.values())

    @staticmethod
    def _to_job(row: str, *, company: str, base: str) -> Job | None:
        title = _TITLE.search(row)
        if not title:
            return None
        location = _LOCATION.search(row)
        date = _DATE.search(row)
        loc = " ".join(html_to_text(location.group(1)).split()) if location else ""
        url = f"{base}{title.group('href')}"
        return Job(
            company=company,
            title=" ".join(html_to_text(title.group("title")).split()),
            url=url,
            source="successfactors",
            external_id=title.group("id"),
            posted_at=parse_timestamp(html_to_text(date.group(1)).strip()) if date else None,
            precision=Precision.DATE_ONLY,
            locations=(loc,) if loc else (),
            remote=detect_remote(loc),
            detail_url=url,
        )

    def fetch_details(self, job: Job) -> Job:
        page = self._get_text(job.detail_url)
        m = _DESCRIPTION.search(page)
        return replace(job, description=html_to_text(m.group(1) if m else page))
