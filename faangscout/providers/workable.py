"""Workable job boards (apply.workable.com/{account}).

``POST https://apply.workable.com/api/v3/accounts/{account}/jobs`` lists
published jobs 20-ish at a time (``nextPage`` token), each with a
``published`` timestamp, location(s), workplace and department - seen for
Elevate K-12's board ("fullmind", 2026-09-27). The full ad comes from
``GET .../api/v2/accounts/{account}/jobs/{shortcode}`` (description +
requirements), fetched only for jobs that pass the cheaper filters.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import FetchHints, Job, Precision
from ..normalize import html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_BASE = "https://apply.workable.com"
_MAX_PAGES = 30


def _place(loc: dict) -> str:
    return ", ".join(p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p)


@register("workable")
class WorkableProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        account = config.get("account")
        if not account:
            raise ProviderError("workable: config requires 'account'")
        base = config.get("base_url", _BASE).rstrip("/")
        company = config.get("company_name", account)
        url = f"{base}/api/v3/accounts/{account}/jobs"

        jobs: dict[str, Job] = {}
        body: dict = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        for _ in range(_MAX_PAGES):
            payload = self._request_json("POST", url, json=body)
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise ProviderError(f"workable: {account} response has no 'results' list")
            for item in results:
                if isinstance(item, dict) and item.get("state", "published") == "published":
                    job = self._to_job(item, company=company, account=account, base=base)
                    jobs.setdefault(job.external_id, job)
            token = payload.get("nextPage")
            if not token or not results or len(jobs) >= hints.max_results:
                break
            body = {**body, "token": token}
        return list(jobs.values())

    @staticmethod
    def _to_job(item: dict, *, company: str, account: str, base: str) -> Job:
        shortcode = str(item.get("shortcode") or item.get("id") or "")
        locs = [loc for loc in item.get("locations") or [item.get("location") or {}] if isinstance(loc, dict)]
        locations = tuple(dict.fromkeys(p for p in (_place(loc) for loc in locs) if p))
        department = item.get("department")
        workplace = item.get("workplace")
        return Job(
            company=company,
            title=(item.get("title") or "").strip(),
            url=f"{base}/{account}/j/{shortcode}/",
            source="workable",
            external_id=shortcode,
            posted_at=parse_timestamp(item.get("published")),
            precision=Precision.EXACT,
            locations=locations,
            remote=True if item.get("remote") or workplace == "remote" else (False if workplace == "on_site" else None),
            department=", ".join(department) if isinstance(department, list) else department,
            employment_type=item.get("type"),
            detail_url=f"{base}/api/v2/accounts/{account}/jobs/{shortcode}",
            raw=item,
        )

    def fetch_details(self, job: Job) -> Job:
        payload = self._get_json(job.detail_url)
        if not isinstance(payload, dict):
            return job
        text = "\n\n".join(html_to_text(payload.get(k)) for k in ("description", "requirements", "benefits") if payload.get(k))
        return replace(job, description=text)
