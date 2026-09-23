"""Markdown rendering of a scout run - for a GitHub step summary or issue comment."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import Job, Precision, Rejection, ScoutReport


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


#: Pipeline stages in the order jobs pass through them. A job rejected at a
#: stage got through every stage before it.
_STAGES = ("posted_within", "role", "location", "experience", "already_sent")
_STAGE_OF = {"semantic": "role"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def company_summary(
    report: ScoutReport, *, hours: float | None = None, location: str | None = None,
    experience: float | None = None,
) -> list[tuple[str, str]]:
    """``(company, why)`` for every company asked for that has no row in the report.

    Reads the funnel from the rejections: a company's jobs that reached a
    stage are the ones kept plus those rejected at that stage or later - so
    the line names the first stage where everything was dropped ("2 new
    India software roles; all need more experience (5+ yrs)").
    """
    with_rows = {sj.job.company for sj in report.jobs}
    rejected: dict[str, dict[str, list[Rejection]]] = {}
    for r in report.rejections:
        stage = _STAGE_OF.get(r.filter_name, r.filter_name)
        rejected.setdefault(r.job.company, {}).setdefault(stage, []).append(r)
    sources: dict[str, list] = {}
    for s in report.sources:
        sources.setdefault(s.company, []).append(s)
    window = f"the last {hours:g}h" if hours else "this window"

    out: list[tuple[str, str]] = []
    for company in report.companies:
        name = company.name
        if name in with_rows:
            continue
        if not company.resolved:
            out.append((name, f"not covered ({company.note})" if company.note else "not covered (no job board found)"))
            continue
        srcs = sources.get(name, [])
        if srcs and all(not s.ok for s in srcs):
            out.append((name, f"couldn't check the board ({(srcs[0].error or '')[:90]})"))
            continue
        by_stage = rejected.get(name, {})

        def reached(stage: str) -> int:
            return sum(len(by_stage.get(s, [])) for s in _STAGES[_STAGES.index(stage):])

        undated = any(r.job.precision is Precision.FIRST_SEEN for r in by_stage.get("posted_within", []))
        if reached("role") == 0:
            why = f"no new postings in {window}"
            if undated:
                why += " (this board shows no dates; postings count as new the day they first appear)"
        elif reached("location") == 0:
            why = f"{_plural(reached('role'), 'new posting')}, none software roles"
        elif reached("experience") == 0:
            why = f"{_plural(reached('location'), 'new software role')}, none in {location or 'the location asked for'}"
        elif reached("already_sent") == 0:
            labels = sorted(
                {r.job.experience for r in by_stage["experience"] if r.job.experience and r.job.experience.min_years is not None},
                key=lambda e: e.min_years,
            )
            needs = ", ".join(dict.fromkeys(e.label() for e in labels[:3]))
            n = reached("experience")
            verdict = "none fits" if n > 1 else "doesn't fit"
            wanted = f"{experience:g} yrs" if experience is not None else "the experience asked for"
            where = f" {location}" if location else ""
            why = f"{_plural(n, f'new{where} software role')}, {verdict} {wanted}"
            if needs:
                why += f" (needs {needs})"
        else:
            why = f"{_plural(reached('already_sent'), 'matching role')}, already sent in an earlier email"
        out.append((name, why))
    return out


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
    show_excluded: bool = False,
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
                exp = f" {_cell(job.experience.display()) if job.experience else '—'} |"
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

    summary = company_summary(report, hours=hours, location=location, experience=experience)
    if summary:
        lines += ["", f"**No match today ({len(summary)}):**", ""]
        lines += [f"- **{_cell(name)}**: {_cell(why)}" for name, why in summary]
    elif report.unresolved:
        lines += ["", f"**Not covered yet ({len(report.unresolved)}):** {', '.join(report.unresolved)}"]

    notes = [w for w in report.warnings if not w.startswith("could not resolve")]
    if notes:
        lines += [""] + [f"> ⚠️ {_cell(w)}" for w in notes]

    # Jobs that got as far as the experience check but didn't fit - worth
    # seeing, so a misread requirement is visible rather than silent.
    excluded = [r for r in report.rejections if r.filter_name == "experience"]
    if show_excluded and excluded:
        lines += ["", f"<details><summary>Excluded by experience ({len(excluded)})</summary>", ""]
        lines += [f"- {_cell(r.job.company)}: [{_cell(r.job.title)}]({r.job.url}) - {_cell(r.reason)}" for r in excluded]
        lines += ["", "</details>"]

    return "\n".join(lines) + "\n"
