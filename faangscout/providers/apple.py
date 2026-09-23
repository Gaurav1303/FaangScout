"""Apple's careers site (jobs.apple.com).

Search results are server-rendered: each row has a link
``/{locale}/details/{id}/{slug}``, a ``team-name``, a ``job-posted-date``
("23 Sept 2026") and a location span - seen 2026-09-23. With
``sort=newest`` paging (``page=N``) stops once a whole page predates the
time window.

Apple's worldwide "newest" list is dominated by retail roles posted per
country, so when the search has a location filter that the board config
maps to an Apple location code (``location_codes: {india: india-INDC}``),
the search is narrowed to it server-side.

Posting ids aren't always plain digits (some carry a suffix such as
``114438210-3337``), so the id is anything up to the next slash; the slug after it is optional.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, register

_BASE = "https://jobs.apple.com"
_ROW = re.compile(r'<h3><a [^>]*href="(?P<href>/[^"]+/details/(?P<id>[^/"?]+)(?:/[^"?]*)?)[^"]*"[^>]*>(?P<title>.*?)</a></h3>(?P<rest>.*?)(?=<h3><a |$)', re.S)
_TEAM = re.compile(r'class="team-name[^"]*">(.*?)</span>', re.S)
_DATE = re.compile(r'class="job-posted-date"[^>]*>(.*?)</span>', re.S)
_LOCATION = re.compile(r'class="table--advanced-search__location-sub"[^>]*>(.*?)</span>', re.S)
_MAX_PAGES = 20
_DAY = timedelta(days=1)


@register("apple_jobs")
class AppleJobsProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        base = config.get("base_url", _BASE).rstrip("/")
        locale = config.get("locale", "en-us")
        company = config.get("company_name", "Apple")
        params: dict[str, object] = {"search": hints.role_query or "", "sort": "newest"}
        codes = {str(k).lower(): v for k, v in (config.get("location_codes") or {}).items()}
        location = config.get("location") or codes.get((hints.location or "").strip().lower())
        if location:
            params["location"] = location

        jobs: dict[str, Job] = {}
        for page in range(1, _MAX_PAGES + 1):
            html = self._get_text(f"{base}/{locale}/search", params={**params, "page": page})
            rows = [self._to_job(m, company=company, base=base) for m in _ROW.finditer(html)]
            new = [j for j in rows if j.external_id not in jobs]
            for job in new:
                jobs[job.external_id] = job
            if not new or len(jobs) >= hints.max_results:
                break
            if hints.since and all(j.posted_at and j.posted_at + _DAY < hints.since for j in rows):
                break
        return list(jobs.values())

    @staticmethod
    def _to_job(m: re.Match, *, company: str, base: str) -> Job:
        rest = m.group("rest")
        team = _TEAM.search(rest)
        date = _DATE.search(rest)
        location = _LOCATION.search(rest)
        loc = " ".join(html_to_text(location.group(1)).split()) if location else ""
        return Job(
            company=company,
            title=" ".join(html_to_text(m.group("title")).split()),
            url=f"{base}{m.group('href')}",
            source="apple_jobs",
            external_id=m.group("id"),
            posted_at=parse_timestamp(html_to_text(date.group(1)).strip()) if date else None,
            precision=Precision.DATE_ONLY,
            locations=(loc,) if loc else (),
            remote=detect_remote(loc),
            department=html_to_text(team.group(1)).strip() if team else None,
            detail_url=f"{base}{m.group('href')}",
        )

    def fetch_details(self, job: Job) -> Job:
        return replace(job, description=html_to_text(self._get_text(job.detail_url)))
