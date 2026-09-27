"""Kula career sites (careers.kula.ai/{account}) - Slice, Acko.

Their careers pages embed Kula, which loads every listed job in one call:
``GET https://careers.kula.ai/api/internal/ats_job_posts?accountName={account}&page=N&type=ats_job_post.index&items=99``
- seen in browser captures of slice.bank.in and acko.com (2026-09-27). Each
post carries ``launch_at`` (when it opened; exact), the full description,
department, workplace and offices with a readable ``location`` ("Bengaluru,
Karnataka, India"). ``meta.pages`` says how many pages there are.

The job page is ``careers.kula.ai/{account}/{id}`` (it redirects to a slugged
URL).
"""

from __future__ import annotations

from ..models import FetchHints, Job, Precision
from ..normalize import html_to_text, parse_timestamp
from .base import Provider, ProviderError, register

_BASE = "https://careers.kula.ai"
_PAGE_SIZE = 99
_MAX_PAGES = 10


@register("kula")
class KulaProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        account = config.get("account")
        if not account:
            raise ProviderError("kula: config requires 'account'")
        base = config.get("base_url", _BASE).rstrip("/")
        company = config.get("company_name", account)

        jobs: list[Job] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = self._get_json(f"{base}/api/internal/ats_job_posts", params={
                "accountName": account, "page": page, "type": "ats_job_post.index", "items": _PAGE_SIZE})
            posts = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(posts, list):
                raise ProviderError(f"kula: {account} response has no 'data' list")
            jobs.extend(self._to_job(p, company=company, account=account, base=base)
                        for p in posts if isinstance(p, dict) and p.get("listed", True))
            pages = ((payload.get("meta") or {}).get("pages")) or 1
            if page >= pages or not posts:
                break
        return jobs

    @staticmethod
    def _to_job(post: dict, *, company: str, account: str, base: str) -> Job:
        ats = post.get("ats_job") or {}
        offices = [o for o in ats.get("offices") or [] if isinstance(o, dict)]
        locations = tuple(dict.fromkeys(loc for loc in (o.get("location") or o.get("name") for o in offices) if loc))
        workplace = ats.get("workplace")
        return Job(
            company=company,
            title=(post.get("title") or "").strip(),
            url=f"{base}/{account}/{post.get('id')}",
            source="kula",
            external_id=str(post.get("id") or ""),
            posted_at=parse_timestamp(post.get("launch_at")),
            precision=Precision.EXACT,
            locations=locations,
            remote=True if workplace == "remote" or any(o.get("remote") for o in offices) else (
                False if workplace == "office" else None),
            department=(ats.get("ats_department") or {}).get("name"),
            employment_type=ats.get("employment_type"),
            description=html_to_text(ats.get("job_description")),
            raw={k: v for k, v in post.items() if k != "ats_job"},
        )
