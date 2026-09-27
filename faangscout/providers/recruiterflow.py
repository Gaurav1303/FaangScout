"""Recruiterflow career pages (recruiterflow.com/{company}/jobs) - CoinSwitch.

The page embeds its job list as JSON - ``window.jobsList = {...};`` - grouped
by department: ``{"department": [[name, [job, ...]], ...]}`` where each job has
``job_id``, ``job_name``, ``details`` (the location, in this grouping),
``employment_type``, ``apply_link`` and ``last_opened`` - seen on
CoinSwitch's page (2026-09-27).

``last_opened`` is when the posting was last (re)opened, so jobs are
``Precision.APPROXIMATE``. The full ad is the job page's text.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_BASE = "https://recruiterflow.com"
_JOBS_LIST = re.compile(r"window\.jobsList\s*=\s*")


def jobs_list(html: str) -> dict:
    """The ``window.jobsList`` object embedded in a Recruiterflow careers page."""
    m = _JOBS_LIST.search(html)
    if not m:
        raise ProviderError("recruiterflow: page has no window.jobsList (layout changed?)")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError as exc:
        raise ProviderError(f"recruiterflow: window.jobsList is not valid JSON ({exc})") from exc
    return data if isinstance(data, dict) else {}


@register("recruiterflow")
class RecruiterflowProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        slug = config.get("company")
        if not slug:
            raise ProviderError("recruiterflow: config requires 'company'")
        base = config.get("base_url", _BASE).rstrip("/")
        company = config.get("company_name", slug)
        data = jobs_list(self._get_text(f"{base}/{slug}/jobs"))

        jobs: dict[str, Job] = {}
        for group in data.get("department") or []:
            if not (isinstance(group, list) and len(group) == 2 and isinstance(group[1], list)):
                continue
            department, entries = group
            for entry in entries:
                if isinstance(entry, dict):
                    job = self._to_job(entry, company=company, department=department, base=base)
                    jobs.setdefault(job.external_id, job)
        return list(jobs.values())

    @staticmethod
    def _to_job(entry: dict, *, company: str, department: str, base: str) -> Job:
        location = (entry.get("details") or "").strip()
        url = f"{base}/{str(entry.get('apply_link') or '').lstrip('/')}"
        return Job(
            company=company,
            title=(entry.get("job_name") or "").strip(),
            url=url,
            source="recruiterflow",
            external_id=str(entry.get("job_id") or entry.get("apply_link") or ""),
            posted_at=parse_timestamp(entry.get("last_opened")),
            precision=Precision.APPROXIMATE,
            locations=(location,) if location else (),
            remote=True if entry.get("remote_type") == "remote" else detect_remote(location),
            department=department,
            employment_type=entry.get("employment_type"),
            detail_url=url,
            raw=entry,
        )

    def fetch_details(self, job: Job) -> Job:
        return replace(job, description=html_to_text(self._get_text(job.detail_url)))
