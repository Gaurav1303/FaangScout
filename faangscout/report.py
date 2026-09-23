"""Markdown rendering of a scout run - for a GitHub step summary or issue comment."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import Job, Precision, ScoutReport


def format_age(job: Job, *, now: datetime | None = None) -> str:
    """Human age of a posting, no more precise than the source's timestamp.

    A date-only source (Amazon) knows the day, not the hour - "45h ago" would
    be false precision for a job that may have landed late yesterday.
    """
    if job.posted_at is None:
        return "date unknown"
    now = now or datetime.now(UTC)
    if job.precision is Precision.DATE_ONLY:
        days = (now.date() - job.posted_at.date()).days
        return {0: "today", 1: "yesterday"}.get(days, f"{days}d ago")
    hours = (now - job.posted_at).total_seconds() / 3600
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.0f}d ago"


def _cell(text: str) -> str:
    """Keep a value from breaking out of its table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(
    report: ScoutReport,
    *,
    role: str | None = None,
    hours: float | None = None,
    new_only: bool = False,
    max_rows: int | None = None,
    more_link: str | None = None,
    location: str | None = None,
    experience: float | None = None,
) -> str:
    """Render ``report`` as Markdown.

    ``max_rows`` caps the table (GitHub rejects issue comments over 65,536
    characters - roughly 300 rows); the overflow is counted, and pointed at
    ``more_link`` when one is given.
    """
    jobs = [sj.job for sj in report.jobs]
    noun = "new opening" if new_only else "opening"
    heading = f"## FaangScout: {len(jobs)} {noun}{'' if len(jobs) == 1 else 's'}"
    scope = []
    if role:
        scope.append(f"role: **{_cell(role)}**")
    if location:
        scope.append(f"in **{_cell(location)}**")
    if experience is not None:
        scope.append(f"fits **{experience:g} yrs** experience")
    if hours:
        scope.append(f"posted in the last **{hours:g}h**")
    lines = [heading]
    if scope:
        lines += ["", " · ".join(scope)]

    lines.append("")
    if jobs:
        with_exp = any(job.experience is not None for job in jobs)
        header = "| Company | Role | Location | Posted |" + (" Experience |" if with_exp else "") + " Link |"
        lines += [header, "|" + "---|" * (header.count("|") - 1)]
        shown = jobs if max_rows is None else jobs[:max_rows]
        for job in shown:
            exp = ""
            if with_exp:
                exp = f" {_cell(job.experience.label()) if job.experience else '—'} |"
            lines.append(
                f"| {_cell(job.company)} | {_cell(job.title)} | {_cell(job.location_text) or '—'} "
                f"| {format_age(job)} |{exp} [open]({job.url}) |"
            )
        hidden = len(jobs) - len(shown)
        if hidden:
            where = f" - [full list]({more_link})" if more_link else ""
            lines += ["", f"_…and {hidden} more{where}._"]
    else:
        lines.append("_No matching openings in this window._")

    ok_boards = [s for s in report.sources if s.ok]
    lines += ["", f"Checked {len(ok_boards)}/{len(report.sources)} boards successfully."]

    errors = report.errors
    if errors:
        lines += ["", f"<details><summary>Unreachable boards ({len(errors)})</summary>", ""]
        lines += [f"- **{_cell(e.company)}** (`{e.source}`): {_cell(e.error or '')}" for e in errors]
        lines += ["", "</details>"]

    if report.unresolved:
        lines += ["", f"**Not covered yet ({len(report.unresolved)}):** {', '.join(report.unresolved)}"]

    return "\n".join(lines) + "\n"
