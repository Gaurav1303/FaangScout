"""Jobvite career sites (jobs.jobvite.com/{company}) - Nutanix.

The list page is server-rendered: under each ``<h3 class="h2">Department</h3>``
a ``<table class="jv-job-list">`` holds rows of ``jv-job-list-name`` (a link
to ``/{company}/job/{id}``) and ``jv-job-list-location``. Seen on Nutanix's
board (2026-09-23), reachable directly even though careers.nutanix.com sits
behind a bot check.

No posting dates anywhere, so jobs are ``Precision.FIRST_SEEN``.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text
from .base import Provider, ProviderError, register

_BASE = "https://jobs.jobvite.com"
_SECTION = re.compile(r'<h3 class="h2">(?P<dept>.*?)</h3>\s*<table class="jv-job-list">(?P<body>.*?)</table>', re.S)
_ROW = re.compile(
    r'<td class="jv-job-list-name">\s*<a href="(?P<href>[^"]+/job/[^"]+)">(?P<title>.*?)</a>\s*</td>\s*'
    r'<td class="jv-job-list-location">(?P<loc>.*?)</td>',
    re.S,
)
_DESCRIPTION = re.compile(r'<div class="jv-job-detail-description">(.*?)</div>\s*(?:<div class="jv-job-detail-bottom|</div>)', re.S)


@register("jobvite")
class JobviteProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        slug = config.get("company")
        if not slug:
            raise ProviderError("jobvite: config requires 'company'")
        base = config.get("base_url", _BASE).rstrip("/")
        company = config.get("company_name", slug)
        page = self._get_text(f"{base}/{slug}/jobs")

        jobs: dict[str, Job] = {}
        for section in _SECTION.finditer(page):
            dept = html_to_text(section.group("dept")).strip() or None
            for row in _ROW.finditer(section.group("body")):
                href = row.group("href")
                location = " ".join(html_to_text(row.group("loc")).split())
                jobs.setdefault(href, Job(
                    company=company,
                    title=" ".join(html_to_text(row.group("title")).split()),
                    url=f"{base}{href}",
                    source="jobvite",
                    external_id=href.rsplit("/", 1)[-1],
                    precision=Precision.FIRST_SEEN,
                    locations=(location,) if location else (),
                    remote=detect_remote(location),
                    department=dept,
                    detail_url=f"{base}{href}",
                ))
        if not jobs and "jv-job-list" not in page:
            raise ProviderError(f"jobvite: {slug} page has no job list (layout changed?)")
        return list(jobs.values())

    def fetch_details(self, job: Job) -> Job:
        page = self._get_text(job.detail_url)
        m = _DESCRIPTION.search(page)
        return replace(job, description=html_to_text(m.group(1) if m else page))
