"""Orchestrator: ties companies -> providers -> filters into one search.

This is the only module that knows about all four layers (registry,
providers, models, filters); everything else stays decoupled from the others.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import httpx

from .companies.registry import CompanyRegistry, load_registry
from .companies.resolver import resolve_companies
from .filters import FilterPipeline
from .models import (
    FetchHints,
    Job,
    ResolvedCompany,
    ScoredJob,
    ScoutReport,
    SearchCriteria,
    SourceReport,
)
from .providers.base import ProviderError, get_provider

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 8


def scout(
    criteria: SearchCriteria,
    *,
    registry: CompanyRegistry | None = None,
    probe_unknown: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
    filter_pipeline: FilterPipeline | None = None,
) -> ScoutReport:
    """Run one search: resolve companies, fetch every board, filter, report.

    A single company's fetch failure never aborts the run - it becomes a
    failed :class:`SourceReport` entry, and every other company still gets
    fetched and filtered normally.
    """
    registry = registry or load_registry()
    pipeline = filter_pipeline or FilterPipeline()
    report = ScoutReport(criteria=criteria)

    with httpx.Client(timeout=15.0, follow_redirects=True) as resolve_client:
        resolved = resolve_companies(criteria.companies, registry, probe=probe_unknown, client=resolve_client)

    report.unresolved = [c.query for c in resolved if not c.resolved]

    all_sources = [(company, source) for company in resolved for source in company.sources]
    if not all_sources:
        return _finish(report, [], pipeline, criteria)

    hints = FetchHints(since=criteria.since, role_query=criteria.filters.get("role"))
    fetched_jobs: list[Job] = []

    with httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "FaangScout/0.1"},
    ) as client:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_one, company, source, hints, client): (company, source)
                for company, source in all_sources
            }
            for future in as_completed(futures):
                company, source = futures[future]
                jobs, src_report = future.result()
                report.sources.append(src_report)
                fetched_jobs.extend(jobs)

    return _finish(report, fetched_jobs, pipeline, criteria)


def _fetch_one(
    company: ResolvedCompany, source, hints: FetchHints, client: httpx.Client
) -> tuple[list[Job], SourceReport]:
    started = time.monotonic()
    src_report = SourceReport(company=company.name, source=source.describe())
    try:
        provider = get_provider(source.provider, client=client)
        # The resolved company's canonical name is always the source of truth
        # for display/dedup - it must not depend on whether the registry
        # entry happened to also set company_name in its board config.
        config = {**source.config, "company_name": company.name}
        jobs = provider.fetch(config, hints)
    except ProviderError as exc:
        src_report.error = str(exc)
        return [], _timed(src_report, started)
    except Exception as exc:  # noqa: BLE001 - never let one bad board kill the run
        logger.exception("unexpected error fetching %s / %s", company.name, source.describe())
        src_report.error = f"unexpected error: {exc!r}"
        return [], _timed(src_report, started)

    src_report.fetched = len(jobs)
    return jobs, _timed(src_report, started)


def _timed(src_report: SourceReport, started: float) -> SourceReport:
    src_report.elapsed_ms = int((time.monotonic() - started) * 1000)
    return src_report


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen: dict[tuple[str, str], Job] = {}
    for job in jobs:
        key = job.dedupe_key
        existing = seen.get(key)
        if existing is None:
            seen[key] = job
            continue
        # Prefer the entry with a real timestamp over one without.
        if existing.posted_at is None and job.posted_at is not None:
            seen[key] = job
    return list(seen.values())


def _finish(
    report: ScoutReport, jobs: list[Job], pipeline: FilterPipeline, criteria: SearchCriteria
) -> ScoutReport:
    deduped = _dedupe(jobs)
    kept, rejections, warnings = pipeline.run(deduped, criteria)

    # Most recent first; undated jobs (only present when include_undated=True) sort last.
    _EPOCH = datetime.min.replace(tzinfo=UTC)
    kept.sort(key=lambda j: j.posted_at or _EPOCH, reverse=True)

    if criteria.limit is not None:
        kept = kept[: criteria.limit]

    report.jobs = [ScoredJob(job=j) for j in kept]
    report.rejections = rejections
    report.warnings.extend(warnings)
    if report.unresolved:
        report.warnings.append(
            f"could not resolve: {', '.join(report.unresolved)} "
            "(not in the registry - add it, use the inline 'Name:provider:key=value' "
            "syntax, or pass probe_unknown=True)"
        )
    return report
