"""Command-line entry point: ``faangscout --companies stripe airbnb --role backend``."""

from __future__ import annotations

import argparse
import json
import sys

from .companies.registry import load_registry
from .models import SearchCriteria
from .scout import scout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faangscout",
        description="Scout career pages for openings matching your role, posted in a recent time window.",
    )
    parser.add_argument(
        "--companies",
        "-c",
        nargs="+",
        required=True,
        metavar="NAME",
        help="Company names (e.g. Stripe Airbnb). Also accepts 'Name:provider:key=value' "
        "for a company not in the registry.",
    )
    parser.add_argument("--role", "-r", help="Desired role, e.g. 'backend engineer'")
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Only show jobs posted within this many hours (default: 24). Use --hours 48 for 'last 2 days'.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of results returned")
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a text report")
    parser.add_argument("--explain", action="store_true", help="Also list rejected jobs and why they were dropped")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    criteria = SearchCriteria.build(
        args.companies,
        role=args.role,
        posted_within_hours=args.hours,
        include_undated=args.include_undated,
        limit=args.limit,
        semantic=True if args.semantic else None,
    )
    registry = load_registry(args.companies_file)
    report = scout(criteria, registry=registry, probe_unknown=args.probe)

    if args.json:
        print(json.dumps(_report_to_dict(report, explain=args.explain), indent=2, default=str))
        return 0

    _print_text_report(report, explain=args.explain)
    return 0


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
        age = ""
        if job.posted_at:
            hours = (job.age.total_seconds() / 3600) if job.age else 0
            age = f" ({hours:.1f}h ago)" if hours < 48 else f" ({hours / 24:.1f}d ago)"
        loc = f" [{job.location_text}]" if job.location_text else ""
        print(f"- {job.company}: {job.title}{loc}{age}\n  {job.url}")

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
