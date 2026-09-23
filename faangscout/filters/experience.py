"""Keep jobs a candidate with N years of experience qualifies for.

``criteria.filters["experience"]`` is the candidate's years (e.g. ``3``), or a
dict ``{years: 3, include_unknown: true}``. A job passes when its required
range contains those years: "2+ years" and "3-5 years" pass for 3, "5+
years" and "0-2 years" don't. How the requirement is read is in
``faangscout/experience.py``.

Postings that state no requirement anywhere are kept by default and labelled
"not stated" - dropping them would silently hide real matches.

Years stated in the posting always decide. When none are stated, a job also
passes if its title sits at the wanted SDE level on the company's own ladder
(``sde``, default 2): "Salesforce MTS" and "Walmart Software Engineer III"
are SDE-2 whatever the generic reading of the words would say.

Runs last and asks for full descriptions (``needs_description``), so the
per-job detail requests some boards need happen only for jobs that already
passed every cheaper filter.
"""

from __future__ import annotations

from dataclasses import replace

from ..experience import assess
from ..models import Job, Rejection, SearchCriteria
from .base import Filter, register


DEFAULT_SDE = 2


def parse_config(value) -> tuple[float, bool, int | None]:
    """``3`` or ``{years: 3, include_unknown: true, sde: 2}`` -> (years, include_unknown, sde)."""
    if isinstance(value, dict):
        sde = value.get("sde", DEFAULT_SDE)
        return float(value["years"]), bool(value.get("include_unknown", True)), None if sde is None else int(sde)
    return float(value), True, DEFAULT_SDE


@register("experience")
class ExperienceFilter(Filter):
    order = 80
    needs_description = True

    def apply(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection]]:
        years, include_unknown, sde = parse_config(criteria.filters["experience"])
        kept: list[Job] = []
        rejected: list[Rejection] = []
        for job in jobs:
            req = assess(job)
            job = replace(job, experience=req)
            verdict = req.admits(years)
            at_level = sde is not None and req.basis != "description" and req.sde == sde
            if verdict or at_level or (verdict is None and include_unknown):
                kept.append(job)
            else:
                why = "no stated requirement" if verdict is None else f"requires {req.label()}"
                evidence = f" ({req.evidence!r})" if req.evidence else ""
                rejected.append(Rejection(job, self.name, f"{why}{evidence}"))
        return kept, rejected
