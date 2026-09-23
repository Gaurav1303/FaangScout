"""Core data structures shared by every provider, filter, and the orchestrator.

Nothing in here imports from the rest of the package, so it stays safe to
import from anywhere without circularity.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class Precision(str, Enum):
    """How much to trust ``Job.posted_at``.

    Some boards only expose a "last updated" timestamp (Greenhouse) or a
    coarse relative string (Workday: "Posted 3 Days Ago"). The time-window
    filter needs to know the difference so it can be honest about edge cases.
    """

    EXACT = "exact"
    APPROXIMATE = "approximate"
    #: A calendar date with no time of day (Amazon: "March 5, 2026"). The
    #: timestamp lands at midnight, so the posting really happened anywhere in
    #: the following 24h - the time-window filter widens accordingly rather
    #: than dropping this morning's job because it parsed as "32h ago".
    DATE_ONLY = "date_only"
    #: The board publishes no dates (Jobvite, Rippling ATS). ``posted_at`` is
    #: when FaangScout first saw the posting - see ``first_seen.py``.
    FIRST_SEEN = "first_seen"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExperienceReq:
    """Years of experience a posting asks for, and how we know.

    ``basis`` is "description" (read from the posting text), "title" (implied
    by a level like "Senior" or "Engineer II"), or "unknown". ``max_years`` is
    None for open-ended requirements ("3+ years").
    """

    min_years: float | None = None
    max_years: float | None = None
    basis: str = "unknown"
    evidence: str = ""

    def admits(self, years: float) -> bool | None:
        """Whether someone with ``years`` of experience fits. None if unknown."""
        if self.min_years is None:
            return None
        if years < self.min_years:
            return False
        return self.max_years is None or years <= self.max_years

    def label(self) -> str:
        if self.min_years is None:
            return "not stated"
        lo = f"{self.min_years:g}"
        text = f"{lo}–{self.max_years:g} yrs" if self.max_years is not None else f"{lo}+ yrs"
        return text if self.basis == "description" else f"~{text} (title)"


@dataclass(frozen=True, slots=True)
class Job:
    """A single job posting, normalised across every source."""

    company: str
    title: str
    url: str
    source: str
    external_id: str = ""
    posted_at: datetime | None = None
    precision: Precision = Precision.UNKNOWN
    locations: tuple[str, ...] = ()
    remote: bool | None = None
    department: str | None = None
    employment_type: str | None = None
    description: str = ""
    #: Where the provider can fetch the full posting when the listing didn't
    #: include a description (see ``Provider.fetch_details``).
    detail_url: str = ""
    #: Filled in by the experience filter.
    experience: ExperienceReq | None = None
    # Untouched provider payload, kept for debugging and for filters that want
    # to reach for a field we have not normalised yet.
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.posted_at is not None and self.posted_at.tzinfo is None:
            raise ValueError(f"posted_at must be timezone-aware: {self.posted_at!r}")

    @property
    def age(self) -> timedelta | None:
        if self.posted_at is None:
            return None
        return datetime.now(UTC) - self.posted_at

    @property
    def location_text(self) -> str:
        return ", ".join(self.locations)

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """Same posting reached through two boards should collapse to one row."""
        from .normalize import dedupe_title, slugify

        return (slugify(self.company), dedupe_title(self.title) + "|" + self.location_text.lower())

    def searchable_text(self) -> str:
        parts = [self.title, self.department or "", self.location_text, self.employment_type or ""]
        return " ".join(p for p in parts if p).lower()


@dataclass(frozen=True, slots=True)
class ScoredJob:
    """A job that survived the filter pipeline, plus why it did."""

    job: Job
    score: float = 1.0
    reasons: tuple[str, ...] = ()

    def with_score(self, score: float) -> ScoredJob:
        return replace(self, score=score)


@dataclass(frozen=True, slots=True)
class Rejection:
    """A job that a filter dropped. Surfaced under ``--explain``."""

    job: Job
    filter_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class CompanySource:
    """One board belonging to one company: provider name + provider config."""

    provider: str
    config: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        key = self.config.get("board") or self.config.get("tenant") or self.config.get("token") or ""
        return f"{self.provider}:{key}" if key else self.provider


@dataclass(frozen=True, slots=True)
class ResolvedCompany:
    """A company name from the user mapped onto one or more boards."""

    query: str
    name: str
    sources: tuple[CompanySource, ...] = ()
    careers_url: str | None = None
    # "registry" when it came from companies.yaml, "discovered" when probed live,
    # "unresolved" when we could not find a board at all.
    origin: str = "registry"

    @property
    def resolved(self) -> bool:
        return bool(self.sources)


@dataclass
class SearchCriteria:
    """What the user asked for.

    ``filters`` is deliberately an open dict keyed by filter name: adding a new
    filter means registering a class, not editing this dataclass. ``build()``
    exists purely for ergonomics on the common path.
    """

    companies: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    include_undated: bool = False
    limit: int | None = None

    @classmethod
    def build(
        cls,
        companies: list[str],
        *,
        role: str | None = None,
        posted_within_hours: float | None = 24,
        include_undated: bool = False,
        limit: int | None = None,
        **extra_filters: Any,
    ) -> SearchCriteria:
        filters: dict[str, Any] = {}
        if role:
            filters["role"] = role
        if posted_within_hours is not None:
            filters["posted_within"] = {"hours": posted_within_hours}
        for key, value in extra_filters.items():
            if value not in (None, "", [], {}):
                filters[key] = value
        return cls(
            companies=list(companies),
            filters=filters,
            include_undated=include_undated,
            limit=limit,
        )

    @property
    def window_hours(self) -> float | None:
        cfg = self.filters.get("posted_within")
        if isinstance(cfg, dict):
            value = cfg.get("hours")
            return float(value) if value is not None else None
        if isinstance(cfg, (int, float)):
            return float(cfg)
        return None

    @property
    def since(self) -> datetime | None:
        hours = self.window_hours
        if hours is None:
            return None
        return datetime.now(UTC) - timedelta(hours=hours)


@dataclass(frozen=True, slots=True)
class FetchHints:
    """Optional narrowing passed down to providers.

    A provider may use these to let the board do the work server-side (Amazon
    and Workday both sort by date, Microsoft takes a query string). Hints are
    advisory: the filter pipeline is always the authority on what matches, so a
    provider is free to ignore them entirely.
    """

    since: datetime | None = None
    role_query: str | None = None
    max_results: int = 400


@dataclass
class SourceReport:
    """Per-board outcome, so a partial failure is visible rather than silent."""

    company: str
    source: str
    fetched: int = 0
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ScoutReport:
    """The result of one scout run."""

    jobs: list[ScoredJob] = field(default_factory=list)
    sources: list[SourceReport] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    criteria: SearchCriteria | None = None

    @property
    def total_fetched(self) -> int:
        return sum(s.fetched for s in self.sources)

    @property
    def errors(self) -> list[SourceReport]:
        return [s for s in self.sources if not s.ok]
