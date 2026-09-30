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
are SDE-2 whatever the generic reading of the words would say. Non-engineering
searches set ``sde: null, ladders: false`` - the ladders map engineering titles
only - and fall back to stated years, then generic title words. They can also
allow being over-qualified by a few years (``overqualified_years``) and rule out
titles by word (``exclude_titles: [intern]``).

Runs last and asks for full descriptions (``needs_description``), so the
per-job detail requests some boards need happen only for jobs that already
passed every cheaper filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..experience import assess
from ..models import Job, Rejection, SearchCriteria
from .base import Filter, register


DEFAULT_SDE = 2


@dataclass(frozen=True)
class ExperienceConfig:
    """The ``experience`` filter's settings; defaults are the plain-number case."""

    years: float
    include_unknown: bool = True
    #: Titles at this SDE level on the company ladder pass (None: off).
    sde: int | None = DEFAULT_SDE
    #: Read titles against the company engineering ladders.
    ladders: bool = True
    #: Still a fit when over-qualified by up to this many years ("2-4 yrs" at 5).
    overqualified_years: float = 0
    #: Title words that rule a job out (e.g. ``intern``), whatever it states.
    exclude_titles: tuple[str, ...] = ()


def parse_config(value) -> ExperienceConfig:
    """``3`` or ``{years: 3, include_unknown: true, sde: 2, ladders: true,
    overqualified_years: 0, exclude_titles: []}``. ``sde: null`` turns the level rule off."""
    if not isinstance(value, dict):
        return ExperienceConfig(years=float(value))
    sde = value.get("sde", DEFAULT_SDE)
    return ExperienceConfig(
        years=float(value["years"]),
        include_unknown=bool(value.get("include_unknown", True)),
        sde=None if sde is None else int(sde),
        ladders=bool(value.get("ladders", True)),
        overqualified_years=float(value.get("overqualified_years") or 0),
        exclude_titles=tuple(str(t).lower() for t in value.get("exclude_titles") or ()),
    )


def _excluded_word(title: str, words: tuple[str, ...]) -> str | None:
    for word in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", title or "", re.IGNORECASE):
            return word
    return None


@register("experience")
class ExperienceFilter(Filter):
    order = 80
    needs_description = True

    def apply(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection]]:
        cfg = parse_config(criteria.filters["experience"])
        kept: list[Job] = []
        rejected: list[Rejection] = []
        for job in jobs:
            req = assess(job, use_ladders=cfg.ladders)
            job = replace(job, experience=req)
            word = _excluded_word(job.title, cfg.exclude_titles)
            if word:
                rejected.append(Rejection(job, self.name, f"title says {word!r}"))
                continue
            verdict = req.admits(cfg.years, over=cfg.overqualified_years)
            at_level = cfg.sde is not None and req.basis != "description" and req.sde == cfg.sde
            if verdict or at_level or (verdict is None and cfg.include_unknown):
                kept.append(job)
            else:
                why = "no stated requirement" if verdict is None else f"requires {req.label()}"
                evidence = f" ({req.evidence!r})" if req.evidence else ""
                rejected.append(Rejection(job, self.name, f"{why}{evidence}"))
        return kept, rejected
