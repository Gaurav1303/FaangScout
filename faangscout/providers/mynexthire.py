"""MyNextHire career sites ({tenant}.mynexthire.com) - Swiggy.

The careers page loads every open requisition in one request:
``POST https://{tenant}.mynexthire.com/employer/careers/reqlist/get`` with
``{"source": "careers", "code": "", "filterByBuId": -1}`` - found in a
browser capture of careers.swiggy.com (2026-09-23). Each entry has the
title, ``expMin``/``expMax`` years, office address, the full description
(``jdDisplay``) and ``approvedOn`` (when it opened; exact).

The stated experience range is written at the top of the description so the
experience filter reads it like any other stated requirement.

No job link is returned; links use MyNextHire's job page, whose ``p``
parameter is base64-encoded JSON naming the requisition.
"""

from __future__ import annotations

import base64
import json

from ..models import FetchHints, Job, Precision
from ..normalize import detect_remote, html_to_text, parse_timestamp
from .base import Provider, ProviderError, register


def job_link(tenant: str, req_id: object) -> str:
    state = {"pageType": "jd", "cvSource": "careers", "reqId": req_id,
             "requester": {"id": "", "code": "", "name": ""}, "page": "careers",
             "bufilter": -1, "customFields": {}}
    token = base64.b64encode(json.dumps(state, separators=(",", ":")).encode()).decode()
    return f"https://{tenant}.mynexthire.com/employer/jobs?src=careers&p={token}"


def _years(value: object) -> str:
    return f"{float(value):g}" if isinstance(value, (int, float)) else ""


@register("mynexthire")
class MyNextHireProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        tenant = config.get("tenant")
        if not tenant:
            raise ProviderError("mynexthire: config requires 'tenant'")
        url = config.get("url", f"https://{tenant}.mynexthire.com/employer/careers/reqlist/get")
        payload = self._request_json("POST", url, json={"source": "careers", "code": "", "filterByBuId": -1})
        reqs = payload.get("reqDetailsBOList") if isinstance(payload, dict) else None
        if not isinstance(reqs, list):
            raise ProviderError(f"mynexthire: {tenant} response has no reqDetailsBOList")
        company = config.get("company_name", tenant)
        return [self._to_job(r, company=company, tenant=tenant) for r in reqs if isinstance(r, dict)]

    @staticmethod
    def _to_job(req: dict, *, company: str, tenant: str) -> Job:
        lo, hi = _years(req.get("expMin")), _years(req.get("expMax"))
        stated = f"Experience required: {lo}-{hi} years" if lo and hi else ""
        # "location" is an office name ("Sumadhura Capitol Towers"); the address
        # names the city, which is what the location filter can recognise.
        place = (req.get("locationAddress") or req.get("location") or "").strip()
        return Job(
            company=company,
            title=(req.get("reqTitle") or req.get("designation") or "").strip(),
            url=job_link(tenant, req.get("reqId")),
            source="mynexthire",
            external_id=str(req.get("reqId") or ""),
            posted_at=parse_timestamp(req.get("approvedOn")),
            precision=Precision.EXACT,
            locations=(place,) if place else (),
            remote=detect_remote(place),
            department=req.get("careerStream") or req.get("buName"),
            employment_type=req.get("employmentType"),
            description="\n".join(p for p in (stated, html_to_text(req.get("jdDisplay"))) if p),
            raw={k: v for k, v in req.items() if k != "jdDisplay"},
        )
