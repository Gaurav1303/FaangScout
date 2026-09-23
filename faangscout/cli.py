"""Command-line entry point: ``faangscout --companies stripe airbnb --role backend``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .check import check_sources
from .companies.registry import load_registry
from .discover import discover, to_registry_yaml
from .first_seen import FirstSeenStore
from .models import ScoredJob, SearchCriteria
from .report import format_age, render_markdown
from .scout import scout
from .seen import SeenStore

DEFAULT_HOURS = 24.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faangscout",
        description="Scout career pages for openings matching your role, posted in a recent time window.",
    )
    parser.add_argument(
        "--companies",
        "-c",
        nargs="+",
        metavar="NAME",
        help="Company names (e.g. Stripe Airbnb). Also accepts 'Name:provider:key=value' "
        "for a company not in the registry.",
    )
    parser.add_argument("--role", "-r", help="Desired role, e.g. 'backend engineer'")
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Only show jobs posted within this many hours (default: 24). Use --hours 48 for 'last 2 days'.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of results returned")
    parser.add_argument(
        "--location",
        help="Only jobs in this country, e.g. 'India' (matches country names, ISO codes, and major cities)",
    )
    parser.add_argument(
        "--experience",
        type=float,
        metavar="YEARS",
        help="Only jobs a candidate with this many years of experience qualifies for, e.g. 3 "
        "('2+ years' and '3-5 years' pass; '5+ years' doesn't). Postings stating no requirement are kept.",
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="Include jobs whose posting date could not be determined instead of dropping them",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Use Claude to judge role fit instead of keyword matching (requires an Anthropic API key)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="For companies not in the registry, try guessing their Greenhouse/Lever/Ashby board",
    )
    parser.add_argument(
        "--companies-file",
        help="Path to a YAML overrides file merged on top of the bundled registry "
        "(same as FAANGSCOUT_COMPANIES_FILE)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diagnose reachability instead of searching: for each company, report whether its "
        "career portal responds, how many postings came back, and whether they carry usable dates",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="YAML file with companies/role/hours (e.g. scout.yaml). Flags given on the command line win.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Probe for the job boards of companies that have none configured, and write the hits "
        "as a registry overrides file (see --out)",
    )
    parser.add_argument(
        "--out",
        default="discovered.yaml",
        metavar="PATH",
        help="Where --discover writes its results (default: discovered.yaml). Feed it back with --companies-file.",
    )
    parser.add_argument(
        "--seen-file",
        metavar="PATH",
        help="JSON file of already-reported postings. Only jobs not in it are reported, and they are "
        "added to it - so a daily run never repeats a job",
    )
    parser.add_argument(
        "--first-seen-file",
        metavar="PATH",
        help="JSON file recording when each posting was first seen - the posting date for boards "
        "that publish none (Jobvite, Rippling). A board's existing backlog is recorded undated on "
        "its first run, so only later postings count as new",
    )
    parser.add_argument("--markdown", action="store_true", help="Print results as a Markdown table")
    parser.add_argument(
        "--comment-out",
        metavar="PATH",
        help="Also write a row-capped Markdown version (see --max-rows) sized for a GitHub issue comment",
    )
    parser.add_argument("--max-rows", type=int, default=100, help="Row cap for --comment-out (default: 100)")
    parser.add_argument("--more-link", metavar="URL", help="Where --comment-out points for the rows it cut")
    parser.add_argument("--json-out", metavar="PATH", help="Also write the full results as JSON to this file")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a text report")
    parser.add_argument("--explain", action="store_true", help="Also list rejected jobs and why they were dropped")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        apply_config(args)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(f"--config: {exc}")
    if not args.companies:
        parser.error("no companies given - pass --companies or a --config file that lists them")

    if args.discover:
        return _run_discover(args)
    if args.check:
        return _run_check(args)

    criteria = SearchCriteria.build(
        args.companies,
        role=args.role,
        posted_within_hours=args.hours,
        include_undated=args.include_undated,
        limit=args.limit,
        semantic=True if args.semantic else None,
        location=args.location,
        experience=args.experience,
    )
    registry = load_registry(args.companies_file)
    first_seen = FirstSeenStore(args.first_seen_file) if args.first_seen_file else None
    report = scout(criteria, registry=registry, probe_unknown=args.probe, first_seen=first_seen)
    if first_seen is not None:
        first_seen.save()

    if args.seen_file:
        store = SeenStore(args.seen_file)
        new_jobs = store.filter_new([sj.job for sj in report.jobs])
        report.jobs = [ScoredJob(job=j) for j in new_jobs]
        store.mark(new_jobs)
        store.save()

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(_report_to_dict(report, explain=args.explain), indent=2, default=str)
        )

    new_only = bool(args.seen_file)
    if args.comment_out:
        Path(args.comment_out).write_text(
            render_markdown(report, role=args.role, hours=args.hours, new_only=new_only,
                            location=args.location, experience=args.experience,
                            max_rows=args.max_rows, more_link=args.more_link)
        )

    if args.markdown:
        print(render_markdown(report, role=args.role, hours=args.hours, new_only=new_only,
                              location=args.location, experience=args.experience,
                              show_excluded=True), end="")
    elif args.json:
        print(json.dumps(_report_to_dict(report, explain=args.explain), indent=2, default=str))
    else:
        _print_text_report(report, explain=args.explain)
    return 0


def apply_config(args: argparse.Namespace) -> None:
    """Fill unset arguments from ``--config``; anything given on the command line wins.

    Boolean flags can't tell "not given" from "false", so for those the config
    can only switch a behaviour on, never off.
    """
    config: dict = {}
    if args.config:
        config = yaml.safe_load(Path(args.config).read_text()) or {}
        if not isinstance(config, dict):
            raise ValueError(f"{args.config} must contain a YAML mapping")

    if not args.companies:
        companies = config.get("companies") or []
        if isinstance(companies, str):
            companies = [c.strip() for c in companies.split(",")]
        args.companies = _dedupe(str(c).strip() for c in companies if str(c).strip())
    if args.role is None:
        args.role = config.get("role")
    if args.hours is None:
        args.hours = float(config.get("hours", DEFAULT_HOURS))
    if args.limit is None and config.get("limit") is not None:
        args.limit = int(config["limit"])
    if args.location is None and config.get("location"):
        args.location = str(config["location"])
    if args.experience is None and config.get("experience") is not None:
        exp = config["experience"]
        args.experience = float(exp["years"] if isinstance(exp, dict) else exp)
    for flag in ("include_undated", "semantic"):
        if config.get(flag):
            setattr(args, flag, True)


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _run_discover(args) -> int:
    registry = load_registry(args.companies_file)
    results = discover(args.companies, registry)
    Path(args.out).write_text(to_registry_yaml(results))

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "company": r.company,
                        "found": r.found,
                        "provider": r.source.provider if r.source else None,
                        "config": r.source.config if r.source else None,
                        "total_jobs": r.total_jobs,
                        "sample_titles": r.sample_titles,
                        "attempts": [
                            {"provider": a.provider, "config": a.config, "outcome": a.outcome, "detail": a.detail}
                            for a in r.attempts
                        ],
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        if not results:
            print("Every company already has a configured board - nothing to discover.")
        for r in results:
            tried = len(r.attempts)
            if r.found:
                print(f"[FOUND] {r.company} -> {r.source.provider} {r.source.config} "
                      f"({r.total_jobs}+ postings, {tried} probe(s), {r.elapsed_ms}ms)")
                for title in r.sample_titles:
                    print(f"      - {title}")
            else:
                print(f"[NOT FOUND] {r.company} ({tried} probe(s), {r.elapsed_ms}ms)")
                for a in r.attempts[-2:]:
                    print(f"      last tried {a.provider} {a.config}: {a.outcome} {a.detail}")
        found = sum(r.found for r in results)
        print(f"\n{found}/{len(results)} discovered; wrote {args.out}")
    return 0


def _run_check(args) -> int:
    registry = load_registry(args.companies_file)
    results, unresolved = check_sources(
        args.companies, registry=registry, role=args.role, probe_unknown=args.probe
    )

    if args.json:
        print(
            json.dumps(
                {
                    "results": [
                        {
                            "company": r.company,
                            "source": r.source,
                            "status": r.status,
                            "ok": r.ok,
                            "total_jobs": r.total_jobs,
                            "dated_jobs": r.dated_jobs,
                            "sample_titles": r.sample_titles,
                            "error": r.error,
                            "elapsed_ms": r.elapsed_ms,
                        }
                        for r in results
                    ],
                    "unresolved": unresolved,
                },
                indent=2,
            )
        )
        return 0 if all(r.ok for r in results) and not unresolved else 1

    if not results and not unresolved:
        print("Nothing to check.")
        return 1

    for r in results:
        print(f"[{r.status}] {r.company} ({r.source}) - {r.elapsed_ms}ms")
        if r.error:
            print(f"    error: {r.error}")
        else:
            print(f"    {r.total_jobs} posting(s) returned, {r.dated_jobs} with a usable date")
            for title in r.sample_titles:
                print(f"      - {title}")

    for name in unresolved:
        print(f"[UNRESOLVED] {name} - no board configured for this company")

    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} board(s) reachable.")
    return 0 if not failed and not unresolved else 1


def _report_to_dict(report, *, explain: bool) -> dict:
    out = {
        "jobs": [
            {
                "company": sj.job.company,
                "title": sj.job.title,
                "url": sj.job.url,
                "source": sj.job.source,
                "posted_at": sj.job.posted_at,
                "precision": sj.job.precision.value,
                "locations": list(sj.job.locations),
                "remote": sj.job.remote,
                "experience": (
                    {
                        "label": sj.job.experience.label(),
                        "min_years": sj.job.experience.min_years,
                        "max_years": sj.job.experience.max_years,
                        "basis": sj.job.experience.basis,
                        "evidence": sj.job.experience.evidence,
                    }
                    if sj.job.experience
                    else None
                ),
            }
            for sj in report.jobs
        ],
        "sources": [
            {"company": s.company, "source": s.source, "fetched": s.fetched, "error": s.error}
            for s in report.sources
        ],
        "unresolved": report.unresolved,
        "warnings": report.warnings,
    }
    if explain:
        out["rejections"] = [
            {"company": r.job.company, "title": r.job.title, "filter": r.filter_name, "reason": r.reason}
            for r in report.rejections
        ]
    return out


def _print_text_report(report, *, explain: bool) -> None:
    if not report.jobs:
        print("No matching jobs found.")
    for sj in report.jobs:
        job = sj.job
        age = f" ({format_age(job)})" if job.posted_at else ""
        loc = f" [{job.location_text}]" if job.location_text else ""
        exp = f" <{job.experience.label()}>" if job.experience else ""
        print(f"- {job.company}: {job.title}{loc}{age}{exp}\n  {job.url}")

    print(f"\n{len(report.jobs)} job(s) across {len(report.sources)} board(s).")

    errors = report.errors
    if errors:
        print("\nSource errors:")
        for e in errors:
            print(f"  ! {e.company} ({e.source}): {e.error}")

    if report.unresolved:
        print(f"\nUnresolved companies: {', '.join(report.unresolved)}")

    for warning in report.warnings:
        if not any(warning.startswith(pfx) for pfx in ("could not resolve",)):
            print(f"\nWarning: {warning}")

    if explain and report.rejections:
        print("\nRejected:")
        for r in report.rejections:
            print(f"  - [{r.filter_name}] {r.job.company}: {r.job.title} - {r.reason}")


if __name__ == "__main__":
    sys.exit(main())
