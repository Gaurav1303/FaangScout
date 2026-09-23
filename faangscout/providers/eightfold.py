"""Eightfold AI career sites (Microsoft, Qualcomm, and many others).

Eightfold-hosted career sites serve search from a JSON endpoint on the
company's own careers host:

``https://{host}/api/pcsx/search?domain={domain}&query=...&start=0&sort_by=timestamp``

Confirmed live on 2026-09-23 against ``apply.careers.microsoft.com``
(domain ``microsoft.com``) and ``careers.qualcomm.com`` (``qualcomm.com``).
The older ``/api/apply/v2/jobs`` endpoint answers 403 "Not authorized for
PCSX" on both, so it isn't used.

Each position carries ``postedTs`` (epoch seconds, when it was posted - or
re-posted) and ``creationTs`` (when the requisition was created, often weeks
earlier). ``postedTs`` is what the site itself sorts by and shows, so it is
the one used; a re-post can look new, which the daily run's seen-file absorbs
(postings are keyed by id). Hence ``Precision.APPROXIMATE``.
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, parse_timestamp
from .base import Provider, ProviderError, register

_PATH = "/api/pcsx/search"
#: Hard stop so a board that ignores `start` can't page forever.
_MAX_PAGES = 40


@register("eightfold")
class EightfoldProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        host = config.get("host")
        domain = config.get("domain")
        if not (host and domain):
            raise ProviderError("eightfold: config requires 'host' and 'domain'")

        base = config.get("base_url", f"https://{host}").rstrip("/")
        company = config.get("company_name", domain)
        params: dict[str, object] = {
            "domain": domain,
            "query": hints.role_query or "",
            "location": config.get("location", ""),
            "start": 0,
            "sort_by": "timestamp",
        }

        jobs: list[Job] = []
        for _ in range(_MAX_PAGES):
            data = self._unwrap(self._get_json(base + _PATH, params=params), host)
            batch = data.get("positions") or []
            if not batch:
                break
            page = [self._to_job(p, company=company, base=base) for p in batch]
            jobs.extend(page)
            params["start"] = int(params["start"]) + len(batch)

            count = data.get("count")
            if isinstance(count, int) and int(params["start"]) >= count:
                break
            if len(jobs) >= hints.max_results:
                break
            # Newest-first; once a whole page predates the window, later pages
            # will too. (Only a whole page, in case the order is loose.)
            if hints.since and all(j.posted_at and j.posted_at < hints.since for j in page):
                break
        return jobs

    @staticmethod
    def _unwrap(payload: object, host: str) -> dict:
        if not isinstance(payload, dict):
            raise ProviderError(f"eightfold: {host} returned a non-object response")
        data = payload.get("data")
        if not isinstance(data, dict) or "positions" not in data:
            message = (payload.get("error") or {}).get("message") or payload.get("message") or ""
            raise ProviderError(
                f"eightfold: {host} response has no data.positions"
                + (f" ({message})" if message else "")
            )
        return data

    @staticmethod
    def _to_job(entry: dict, *, company: str, base: str) -> Job:
        job_id = str(entry.get("id") or "")
        path = entry.get("positionUrl") or (f"/careers/job/{job_id}" if job_id else "")
        url = f"{base}{path}" if path.startswith("/") else path

        locations = entry.get("standardizedLocations") or entry.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        option = (entry.get("workLocationOption") or "").lower()
        remote = {"remote": True, "onsite": False}.get(option)
        if remote is None:
            remote = detect_remote(option, " ".join(map(str, locations)))

        return Job(
            company=company,
            title=entry.get("name", ""),
            url=url,
            source="eightfold",
            external_id=str(entry.get("displayJobId") or job_id),
            posted_at=parse_timestamp(entry.get("postedTs") or entry.get("creationTs")),
            precision=Precision.APPROXIMATE,
            locations=tuple(str(loc) for loc in locations if loc),
            remote=remote,
            department=entry.get("department"),
            raw=entry,
        )
