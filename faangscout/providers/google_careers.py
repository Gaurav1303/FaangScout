"""Google careers (google.com/about/careers/applications).

Search results are server-rendered: each job card has an
``<h3 class="QJPWVe">`` title, a ``place`` icon followed by a
``<span class="r0wTof">`` location, the minimum qualifications as a list,
and a ``jobs/results/{id}-{slug}`` link - seen 2026-09-23. ``sort_by=date``
lists newest first; ``location=`` narrows to the search's location filter
server-side; ``page=N`` pages through 20 at a time.

The class names are Google's generated ones and will change some day; a page
with no recognisable cards raises rather than reading as "no jobs".

Cards show no posting date, so jobs are ``Precision.FIRST_SEEN``. A card
lists only the first few qualifications (a "Senior Staff" card can show just
"3 years of experience with ..."), so the requirement is read from the job's
own page, from "Minimum qualifications" on - only for jobs that pass the
cheaper filters.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text
from .base import Provider, ProviderError, register

_BASE = "https://www.google.com/about/careers/applications/"
_CARD = re.compile(r'<h3 class="QJPWVe">(?P<title>.*?)</h3>(?P<rest>.*?)(?=<h3 class="QJPWVe">|$)', re.S)
_LOCATION = re.compile(r'>place</i>\s*<span class="r0wTof[^"]*">(.*?)</span>', re.S)
_LINK = re.compile(r'href="(jobs/results/(\d+)-[^"?]*)')
_MINIMUM = re.compile(r"minimum qualifications", re.I)
_SECTION_END = re.compile(r"about the job|responsibilities", re.I)
_MAX_PAGES = 10


@register("google_careers")
class GoogleCareersProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        base = config.get("base_url", _BASE)
        params: dict[str, object] = {"sort_by": "date"}
        if hints.role_query:
            params["q"] = hints.role_query
        if hints.location:
            params["location"] = hints.location
        company = config.get("company_name", "Google")

        jobs: dict[str, Job] = {}
        for page in range(1, int(config.get("max_pages", _MAX_PAGES)) + 1):
            html = self._get_text(f"{base}jobs/results/", params={**params, "page": page})
            cards = [self._to_job(m, company=company, base=base) for m in _CARD.finditer(html)]
            cards = [c for c in cards if c is not None]
            if page == 1 and not cards and "jobs/results/" not in html:
                raise ProviderError("google_careers: no job cards on the results page (layout changed?)")
            new = [c for c in cards if c.external_id not in jobs]
            for job in new:
                jobs[job.external_id] = job
            if not new or len(jobs) >= hints.max_results:
                break
        return list(jobs.values())

    @staticmethod
    def _to_job(m: re.Match, *, company: str, base: str) -> Job | None:
        rest = m.group("rest")
        link = _LINK.search(rest)
        if not link:
            return None
        locations = tuple(dict.fromkeys(
            loc for raw in _LOCATION.findall(rest)
            for loc in (p.strip() for p in html_to_text(raw).split(";")) if loc
        ))
        url = f"{base}{link.group(1)}"
        return Job(
            company=company,
            title=" ".join(html_to_text(m.group("title")).split()),
            url=url,
            source="google_careers",
            external_id=link.group(2),
            precision=Precision.FIRST_SEEN,
            locations=locations,
            remote=detect_remote(" ".join(locations)),
            detail_url=url,
        )

    def fetch_details(self, job: Job) -> Job:
        """The job page's qualifications (minimum, then preferred) as text."""
        html = self._get_text(job.detail_url)
        start = _MINIMUM.search(html)
        if not start:
            return replace(job, description=html_to_text(html))
        end = _SECTION_END.search(html, start.end())
        chunk = html[start.start(): end.start() if end else start.start() + 8000]
        return replace(job, description=html_to_text(chunk))
