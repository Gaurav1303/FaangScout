"""Connectivity diagnostics: can we actually reach each company's board?

``scout()`` answers "what jobs match"; this answers the prior question "does
this company's career portal respond to us at all, and can we parse it?".
Worth having as its own path because the two failure modes look identical in
a normal run - a board that returns zero jobs and a board that is blocked,
moved, or reshaped both produce an empty result list.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .companies.registry import CompanyRegistry, load_registry
from .companies.resolver import resolve_companies
from .models import FetchHints
from .providers.base import ProviderError, get_provider


@dataclass
class CheckResult:
    """One board's reachability verdict."""

    company: str
    source: str
    ok: bool = False
    total_jobs: int = 0
    sample_titles: list[str] = field(default_factory=list)
    dated_jobs: int = 0
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def status(self) -> str:
        if not self.ok:
            return "UNREACHABLE"
        if self.total_jobs == 0:
            return "REACHABLE (0 jobs)"
        if self.dated_jobs == 0:
            return "REACHABLE (no usable dates)"
        return "OK"


def check_sources(
    companies: list[str],
    *,
    registry: CompanyRegistry | None = None,
    role: str | None = None,
    probe_unknown: bool = False,
    max_workers: int = 8,
) -> tuple[list[CheckResult], list[str]]:
    """Fetch one small page from each company's board and report what happened.

    Returns ``(results, unresolved_company_names)``.
    """
    registry = registry or load_registry()

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resolved = resolve_companies(companies, registry, probe=probe_unknown, client=client)
        unresolved = [c.query for c in resolved if not c.resolved]

        pairs = [(c, s) for c in resolved for s in c.sources]
        if not pairs:
            return [], unresolved

        hints = FetchHints(role_query=role, max_results=25)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(lambda p: _check_one(p[0], p[1], hints, client), pairs))

    return results, unresolved


def _check_one(company, source, hints: FetchHints, client: httpx.Client) -> CheckResult:
    result = CheckResult(company=company.name, source=source.describe())
    started = time.monotonic()
    try:
        provider = get_provider(source.provider, client=client)
        jobs = provider.fetch({**source.config, "company_name": company.name}, hints)
    except ProviderError as exc:
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never itself crash
        result.error = f"unexpected error: {exc!r}"
    else:
        result.ok = True
        result.total_jobs = len(jobs)
        result.dated_jobs = sum(1 for j in jobs if j.posted_at is not None)
        result.sample_titles = [j.title for j in jobs[:3]]
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result
