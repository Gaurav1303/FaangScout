"""Radancy TalentBrew career sites (jobs.intuit.com, ...).

Search results are server-rendered at ``https://{host}/search-jobs/{keyword}``:
one ``<li>`` per posting holding a link ``/job/{city}/{slug}/{org}/{id}``, an
``<h2>`` title, a ``job-location`` span and a ``data-category`` - seen on
Intuit's site (2026-09-23), which reported 156 results over 11 pages of 15.
Paging is ``?p=N``; a page that adds nothing new ends the crawl, so a site
that ignores the parameter costs one extra request rather than a loop.

Palo Alto Networks runs the same platform with a different template
(``section29__...`` classes, an ``<h2 class=...>`` title, links under
``/en/job/``) - the patterns accept both; its ``base_url`` carries the
``/en`` prefix.

Rows carry no date, so jobs are ``Precision.FIRST_SEEN``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import quote

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text
from .base import Provider, ProviderError, register

_ITEM = re.compile(r"<li\b[^>]*>(?P<body>\s*<a\b[^>]*?href=\"(?P<href>(?:/[a-z]{2})?/job/[^\"]+)\".*?)</li>", re.S)
_TITLE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S)
_LOCATION = re.compile(r'<span class="[^"]*(?:job-location|result-location)[^"]*">(.*?)</span>', re.S)
_TOTAL_PAGES = re.compile(r'data-total-pages="(\d+)"')
_MAX_PAGES = 25


@register("talentbrew")
class TalentBrewProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        host = config.get("host")
        if not host:
            raise ProviderError("talentbrew: config requires 'host'")
        base = config.get("base_url", f"https://{host}").rstrip("/")
        # Job links are site-absolute ("/en/job/..."), so they hang off the origin.
        origin = "/".join(base.split("/")[:3])
        keyword = hints.role_query or config.get("keyword", "")
        company = config.get("company_name", host)

        jobs: dict[str, Job] = {}
        total_pages = 1
        for page in range(1, _MAX_PAGES + 1):
            html = self._get_text(f"{base}/search-jobs/{quote(keyword)}", params={"p": page} if page > 1 else None)
            if page == 1:
                m = _TOTAL_PAGES.search(html)
                total_pages = int(m.group(1)) if m else 1
                if "search-results-list" not in html:
                    raise ProviderError(f"talentbrew: {host} page has no search results list (layout changed?)")
            before = len(jobs)
            for item in _ITEM.finditer(html):
                href = item.group("href")
                body = item.group("body")
                title = _TITLE.search(body)
                location = _LOCATION.search(body)
                category = re.search(r"data-category='([^']*)'", item.group(0))
                loc = " ".join(html_to_text(location.group(1)).split()) if location else ""
                page_url = f"{origin}{href}"
                jobs.setdefault(href, Job(
                    company=company,
                    title=" ".join(html_to_text(title.group(1)).split()) if title else "",
                    url=page_url,
                    source="talentbrew",
                    external_id=href.rstrip("/").rsplit("/", 1)[-1],
                    precision=Precision.FIRST_SEEN,
                    locations=(loc,) if loc else (),
                    remote=detect_remote(loc),
                    department=category.group(1) if category else None,
                    detail_url=page_url,
                ))
            if page >= total_pages or len(jobs) == before or len(jobs) >= hints.max_results:
                break
        return list(jobs.values())

    def fetch_details(self, job: Job) -> Job:
        return replace(job, description=html_to_text(self._get_text(job.detail_url)))
