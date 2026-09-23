"""Company engineering ladders: "Salesforce MTS" -> SDE-2, ~1-4 years.

Companies name the same level differently (Salesforce MTS, Walmart Software
Engineer III, Adobe MTS-2, Nutanix MTS-3 are all roughly SDE-2), and the same
word means different things ("Senior Engineer" is SDE-2 at Qualcomm, SDE-3 at
Microsoft). The ladders live in ``companies/data/levels.yaml`` - data, so a
new company or a corrected range never means touching code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml

from ..normalize import collapse


@dataclass(frozen=True, slots=True)
class Level:
    company: str
    name: str
    sde: int
    min_years: float
    max_years: float | None
    pattern: re.Pattern[str]

    @property
    def label(self) -> str:
        return f"{self.company} {self.name}"


def parse_ladders(data: dict) -> dict[str, tuple[Level, ...]]:
    ladders: dict[str, tuple[Level, ...]] = {}
    for company, levels in ((data or {}).get("companies") or {}).items():
        ladders[collapse(company)] = tuple(
            Level(
                company=company,
                name=str(lv["name"]),
                sde=int(lv["sde"]),
                min_years=float(lv["years"][0]),
                max_years=None if lv["years"][1] is None else float(lv["years"][1]),
                pattern=re.compile(lv["match"], re.IGNORECASE),
            )
            for lv in levels
        )
    return ladders


@lru_cache(maxsize=1)
def load_ladders() -> dict[str, tuple[Level, ...]]:
    text = resources.files("faangscout.companies.data").joinpath("levels.yaml").read_text()
    return parse_ladders(yaml.safe_load(text))


def company_level(company: str, title: str) -> Level | None:
    """The first level on ``company``'s ladder whose pattern matches ``title``."""
    for level in load_ladders().get(collapse(company), ()):
        if level.pattern.search(title or ""):
            return level
    return None
