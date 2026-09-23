"""Text and timestamp normalisation.

Job boards are wildly inconsistent: "Sr. Software Engineer II, Backend (Remote)"
and "Senior Backend Engineer" are the same role, and timestamps arrive as ISO
strings, epoch seconds, epoch milliseconds, US date strings, or prose. Every
messy-input problem in the codebase should be solved here so providers and
filters stay boring.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# --------------------------------------------------------------------------- #
# Slugs and titles
# --------------------------------------------------------------------------- #

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

# Dropped from titles before matching: they carry no signal about the role.
_TITLE_NOISE = re.compile(
    r"\b(?:"
    r"remote|hybrid|onsite|on-site|contract|contractor|intern(?:ship)?|"
    r"full[- ]?time|part[- ]?time|new\s+grad(?:uate)?|university\s+grad(?:uate)?|"
    r"[ivx]{1,4}|\d+|l\d|t\d"
    r")\b",
    re.IGNORECASE,
)

#: Canonical form -> the surface strings people and boards actually write.
#: Extend this rather than sprinkling special cases through the filters.
ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "software engineer": (
        "software engineer",
        "software developer",
        "software development engineer",
        "sde",
        "swe",
        "programmer",
        "member of technical staff",
    ),
    "backend": ("backend", "back end", "back-end", "server side", "services", "platform"),
    "frontend": ("frontend", "front end", "front-end", "ui engineer", "web engineer", "client"),
    "fullstack": ("fullstack", "full stack", "full-stack"),
    "mobile": ("mobile", "ios", "android"),
    "machine learning": (
        "machine learning",
        "ml",
        "deep learning",
        "ai engineer",
        "ai/ml",
        "applied scientist",
        "research engineer",
        "research scientist",
    ),
    "data engineer": ("data engineer", "data engineering", "etl", "data platform"),
    "data scientist": ("data scientist", "data science", "analytics"),
    "devops": ("devops", "sre", "site reliability", "infrastructure", "platform engineer", "cloud engineer"),
    "security": ("security", "appsec", "infosec", "cybersecurity"),
    "product manager": ("product manager", "product management", "pm", "tpm", "technical program manager"),
    "designer": ("designer", "design", "ux", "ui/ux", "product design"),
    "qa": ("qa", "quality assurance", "sdet", "test engineer", "quality engineer"),
    "data analyst": ("data analyst", "business analyst", "business intelligence", "bi analyst"),
    "engineering manager": ("engineering manager", "em", "software engineering manager", "dev manager"),
    "solutions architect": ("solutions architect", "solution architect", "sales engineer", "solutions engineer"),
}

#: Groups that narrow a role rather than name it. When a query hits one of
#: these, a job MUST match it - "backend engineer" must not return every title
#: containing "engineer". Groups outside this set name a job family instead.
SPECIALIZATION_GROUPS: frozenset[str] = frozenset(
    {
        "backend",
        "frontend",
        "fullstack",
        "mobile",
        "machine learning",
        "data engineer",
        "data scientist",
        "devops",
        "security",
        "qa",
        "data analyst",
    }
)

#: Loose tokens a family may match on, used ONLY when the query names no
#: specialization. A bare "software engineer" search should surface "Frontend
#: Engineer"; a "backend engineer" search must not.
BROAD_FAMILY_TERMS: dict[str, tuple[str, ...]] = {
    "software engineer": ("engineer", "developer"),
}

#: Seniority bands and the tokens that imply them.
SENIORITY_TOKENS: dict[str, tuple[str, ...]] = {
    "intern": ("intern", "internship", "co-op", "coop", "apprentice"),
    "entry": ("new grad", "new graduate", "university grad", "graduate", "junior", "jr", "associate", "entry level", "i", "l3"),
    "mid": ("ii", "iii", "mid", "l4", "l5"),
    "senior": ("senior", "sr", "sr.", "iv", "l6", "lead"),
    "staff": ("staff", "l7"),
    "principal": ("principal", "distinguished", "fellow", "architect", "l8"),
    "manager": ("manager", "head of", "director", "vp", "president"),
}

_REMOTE_HINTS = ("remote", "work from home", "wfh", "distributed", "anywhere", "virtual")
_ONSITE_HINTS = ("on-site", "onsite", "in office", "in-office")


def slugify(value: str) -> str:
    """``"Meta Platforms, Inc."`` -> ``"meta-platforms-inc"``."""
    return _NON_ALNUM.sub("-", (value or "").strip().lower()).strip("-")


def collapse(value: str) -> str:
    """``"Meta Platforms"`` -> ``"metaplatforms"``. Used for alias lookups."""
    return _NON_ALNUM.sub("", (value or "").strip().lower())


def dedupe_title(title: str) -> str:
    """Light-touch fold for identity comparison: case/punctuation/whitespace only.

    Deliberately does **not** strip level or numeric tokens the way
    :func:`normalize_title` does - "Software Engineer II" and "Software
    Engineer III" are different open reqs, not the same job written two ways.
    Only exact-same-title postings (the same req reached through two board
    integrations) should collapse via this function.
    """
    text = _NON_ALNUM.sub(" ", (title or "").lower())
    return _WHITESPACE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, drop level/noise tokens, collapse spaces."""
    text = (title or "").lower()
    text = text.replace("&", " and ")
    text = _NON_ALNUM.sub(" ", text)
    text = _TITLE_NOISE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def tokenize(value: str) -> list[str]:
    return [t for t in _NON_ALNUM.sub(" ", (value or "").lower()).split() if t]


@dataclass(frozen=True, slots=True)
class RoleQuery:
    """A role search split into the part that narrows and the part that names.

    ``required`` holds terms from every *specialization* group the query hit
    (see :data:`SPECIALIZATION_GROUPS`). When it is non-empty a job must match
    one of them - otherwise "backend engineer" returns every title containing
    "engineer", including "Frontend Engineer".

    ``optional`` holds job-family terms plus the raw query. It decides matches
    only when the query names no specialization at all (a bare "software
    engineer" search), where the broader net is what the user wants.
    """

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    @property
    def all_terms(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.required) | set(self.optional), key=len, reverse=True))

    @property
    def empty(self) -> bool:
        return not self.required and not self.optional


def expand_role_query(role: str) -> RoleQuery:
    """Expand a user's role string into specialization and family terms.

    ``"backend engineer"`` -> required: back end / server side / platform ... ,
    optional: software engineer / sde / swe ... . ``"software engineer"`` ->
    required: (), optional: the family terms plus broad "engineer"/"developer".
    """
    role_lower = (role or "").lower().strip()
    if not role_lower:
        return RoleQuery()

    required: set[str] = set()
    families: set[str] = set()
    hit_families: set[str] = set()

    for canonical, variants in ROLE_SYNONYMS.items():
        hit = canonical in role_lower or any(
            re.search(rf"\b{re.escape(v)}\b", role_lower) for v in variants
        )
        if not hit:
            continue
        if canonical in SPECIALIZATION_GROUPS:
            required.add(canonical)
            required.update(variants)
        else:
            hit_families.add(canonical)
            families.add(canonical)
            families.update(variants)

    optional: set[str] = {role_lower} | families
    normalized = normalize_title(role_lower)
    if normalized:
        optional.add(normalized)

    # Broad tokens only widen an unspecialized query.
    if not required:
        for family in hit_families:
            optional.update(BROAD_FAMILY_TERMS.get(family, ()))

    return RoleQuery(
        required=tuple(sorted(required, key=len, reverse=True)),
        optional=tuple(sorted(optional, key=len, reverse=True)),
    )


def expand_role_terms(role: str) -> list[str]:
    """Every term a role expands to, longest-first (display/debug helper).

    Matching should use :func:`expand_role_query` instead - this flat list
    drops the required/optional distinction that keeps "backend engineer" from
    matching "Frontend Engineer".
    """
    return list(expand_role_query(role).all_terms)


def detect_seniority(title: str) -> set[str]:
    """Best-effort seniority bands implied by a title. May be empty."""
    text = f" {(title or '').lower()} "
    bands: set[str] = set()
    for band, tokens in SENIORITY_TOKENS.items():
        for token in tokens:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
                bands.add(band)
                break
    return bands


def detect_remote(*texts: str) -> bool | None:
    """``True`` remote, ``False`` explicitly onsite, ``None`` unknown."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    if any(hint in blob for hint in _REMOTE_HINTS):
        return True
    if any(hint in blob for hint in _ONSITE_HINTS):
        return False
    return None


def strip_html(value: str | None) -> str:
    """Tags removed, entities decoded, whitespace collapsed to one line."""
    return _WHITESPACE.sub(" ", html_to_text(value)).strip()


_BLOCK_TAG = re.compile(r"<\s*(?:br|/p|/div|/li|li|/h[1-6]|h[1-6]|/ul|/ol|/tr)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_INLINE_SPACE = re.compile(r"[ \t\f\v\u00a0]+")


def html_to_text(value: str | None) -> str:
    """HTML to plain text that keeps line structure (block tags become newlines).

    Decodes entities before *and* after stripping tags: Greenhouse sends
    descriptions HTML-escaped ("&lt;p&gt;"), which a strip-then-decode order
    would turn into literal "<p>" text.
    """
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = _BLOCK_TAG.sub("\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)

_RELATIVE = re.compile(
    r"(?:posted\s+)?(?P<n>\d+)\+?\s*(?P<unit>minute|min|hour|hr|day|week|month)s?\s*ago",
    re.IGNORECASE,
)
_RELATIVE_WORDS = {
    "today": 0,
    "just posted": 0,
    "posted today": 0,
    "yesterday": 1,
    "posted yesterday": 1,
}
_UNIT_DELTA = {
    "minute": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
}


def parse_timestamp(value: object, *, now: datetime | None = None) -> datetime | None:
    """Parse anything a job board might call a date into aware UTC.

    Handles ISO 8601 (with ``Z`` or offset), epoch seconds, epoch milliseconds,
    a pile of human date formats, and relative prose like ``"Posted 3 Days
    Ago"``. Returns ``None`` rather than raising - a missing date is normal and
    the caller decides what to do about it.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    now = now or datetime.now(UTC)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_epoch(float(value))

    text = str(value).strip()
    if not text:
        return None

    # Bare numeric string -> epoch.
    if re.fullmatch(r"-?\d{9,14}", text):
        return _from_epoch(float(text))

    iso = text.replace("Z", "+00:00")
    # Trim fractional seconds longer than 6 digits, which fromisoformat rejects.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    return parse_relative(text, now=now)


def parse_relative(text: str, *, now: datetime | None = None) -> datetime | None:
    """Parse ``"Posted Today"`` / ``"Posted 30+ Days Ago"`` style strings."""
    now = now or datetime.now(UTC)
    lowered = (text or "").strip().lower()
    if not lowered:
        return None

    for phrase, days in _RELATIVE_WORDS.items():
        if phrase in lowered:
            return now - timedelta(days=days)

    match = _RELATIVE.search(lowered)
    if match:
        delta = _UNIT_DELTA[match.group("unit").lower()]
        return now - delta * int(match.group("n"))
    return None


def _from_epoch(value: float) -> datetime | None:
    # Anything past ~year 2286 in seconds is really milliseconds.
    if abs(value) > 1e11:
        value /= 1000.0
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None
