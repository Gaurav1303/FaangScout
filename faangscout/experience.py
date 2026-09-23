"""Work out how many years of experience a posting asks for.

Read from the description first, falling back to the level in the title:

- Every "N+ years", "N-M years", "at least N years", "N years of ... experience"
  in the *required* part of the text is collected, and the strictest minimum
  wins - "5+ years of development, 3+ with Kafka" requires 5. A title open at
  two levels ("Software Engineer 2 / Senior Software Engineer") instead takes
  the most lenient minimum, since either level is hiring.
- Sections headed "Preferred", "Nice to have", "Bonus"... and lines saying
  "preferred"/"a plus" don't count: "5+ years preferred" isn't a requirement.
- Lines offering a higher-degree route ("OR Master's degree AND 3+ years")
  are skipped, so a bachelor's degree is assumed - Microsoft writes every
  requirement this way.
- Numbers over 15 years are ignored ("serving customers for 25 years").

With nothing stated, the title's level gives a rough range: "Engineer II"
~2-6 years, "Senior"/"Lead" 5+, "Staff" 8+, "Principal" 10+. Neither -> unknown.
"""

from __future__ import annotations

import re

from .models import ExperienceReq, Job

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
}
_NUM = r"(\d{1,2}(?:\.\d)?|" + "|".join(_WORDS) + r")"
_YEARS = r"(?:years?|yrs?)\b"
_MAX_PLAUSIBLE = 15

_RANGE = re.compile(rf"{_NUM}\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_YEARS}", re.I)
_PLUS = re.compile(rf"{_NUM}\s*(?:\+|plus\b|or more\b)\s*{_YEARS}", re.I)
_AT_LEAST = re.compile(rf"(?:at least|minimum(?: of)?|min\.?|over|more than)\s*{_NUM}\s*\+?\s*{_YEARS}", re.I)
_PLAIN = re.compile(
    rf"{_NUM}\s*{_YEARS}(?:'|’)?\s*(?:of\s+)?(?:[\w/&+,-]+\s+){{0,6}}?experience"
    rf"|experience\s+(?:of\s+)?{_NUM}\s*\+?\s*{_YEARS}",
    re.I,
)

_PREFERRED_HEADER = re.compile(
    r"^\W*(?:preferred|desired|nice[- ]to[- ]have|good[- ]to[- ]have|bonus|additional|"
    r"plus(?:es)?|it'?s a plus|pluses|extra credit)\b",
    re.I,
)
_REQUIRED_HEADER = re.compile(
    r"^\W*(?:basic|minimum|required|requirements|must[- ]have|qualifications|"
    r"what you(?:'ll)? (?:need|bring)|who you are|you have|about you|skills|"
    r"responsibilities|what you will do|what you'll do|job responsibilities)\b",
    re.I,
)
_INLINE_OPTIONAL = re.compile(r"\b(?:preferred|nice to have|good to have|is a plus|a plus|bonus|ideally)\b", re.I)
_HIGHER_DEGREE = re.compile(r"\b(?:master'?s|masters|ph\.?\s?d|doctorate|mba)\b", re.I)
_NOT_EXPERIENCE = re.compile(
    r"\b(?:founded|history|anniversary|years old|since \d{4}|for over|we have been|decades?|company)\b",
    re.I,
)

#: Title levels -> (min, max) years. Lowest level wins when a title names two.
_TITLE_LEVELS: tuple[tuple[str, re.Pattern[str], float, float | None], ...] = (
    ("intern", re.compile(r"\b(?:intern|internship|co-?op|apprentice)\b", re.I), 0, 0),
    ("entry", re.compile(r"\b(?:new grad(?:uate)?|graduate|junior|jr\.?|entry[- ]level|associate)\b", re.I), 0, 2),
    ("mid", re.compile(r"\b(?:mid[- ]level)\b", re.I), 2, 6),
    ("senior", re.compile(r"\b(?:senior|sr\.?|lead)\b", re.I), 5, None),
    ("staff", re.compile(r"\bstaff\b", re.I), 8, None),
    ("principal", re.compile(r"\b(?:principal|distinguished|fellow|architect)\b", re.I), 10, None),
    ("manager", re.compile(r"\b(?:manager|director|head of|vp)\b", re.I), 5, None),
)
#: "Engineer II", "SDE 2", "MTS-4": numbered levels following a role word.
_NUMBERED_LEVEL = re.compile(
    r"\b(?:engineer|developer|sde|swe|mts|member of technical staff|dev)\s*[-,]?\s*\(?\s*(iii|ii|iv|i|v|[1-5])\b",
    re.I,
)
_NUMBER_LEVELS = {
    "i": ("entry", 0, 2), "1": ("entry", 0, 2),
    "ii": ("mid", 2, 6), "2": ("mid", 2, 6), "iii": ("mid", 2, 6), "3": ("mid", 2, 6),
    "iv": ("senior", 5, None), "4": ("senior", 5, None), "v": ("senior", 5, None), "5": ("senior", 5, None),
}


def _num(text: str) -> float:
    return float(_WORDS.get(text.lower(), text))


def requirement_lines(text: str) -> list[str]:
    """Lines of ``text`` that state requirements (preferred sections removed)."""
    lines: list[str] = []
    in_preferred = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        short = len(stripped) <= 80
        if short and _PREFERRED_HEADER.search(stripped):
            in_preferred = True
            continue
        if short and _REQUIRED_HEADER.search(stripped):
            in_preferred = False
        if not in_preferred:
            lines.append(stripped)
    return lines


def mentions(text: str) -> list[tuple[float, float | None, str]]:
    """Every (min, max, snippet) years-of-experience mention in required lines."""
    found: list[tuple[float, float | None, str]] = []
    for line in requirement_lines(text):
        if _INLINE_OPTIONAL.search(line) or _HIGHER_DEGREE.search(line) or _NOT_EXPERIENCE.search(line):
            continue
        spans: list[tuple[int, int]] = []

        def take(m: re.Match, lo: float, hi: float | None) -> None:
            if any(m.start() < e and s < m.end() for s, e in spans):
                return  # already covered by a more specific pattern
            if lo > _MAX_PLAUSIBLE or (hi is not None and (hi > _MAX_PLAUSIBLE or hi < lo)):
                return
            spans.append((m.start(), m.end()))
            found.append((lo, hi, line[max(0, m.start() - 30): m.end() + 30].strip()))

        for m in _RANGE.finditer(line):
            take(m, _num(m.group(1)), _num(m.group(2)))
        for m in _PLUS.finditer(line):
            take(m, _num(m.group(1)), None)
        for m in _AT_LEAST.finditer(line):
            take(m, _num(m.group(1)), None)
        for m in _PLAIN.finditer(line):
            value = m.group(1) or m.group(2)
            take(m, _num(value), None)
    return found


def title_levels(title: str) -> list[tuple[str, float, float | None]]:
    levels = [(name, lo, hi) for name, pattern, lo, hi in _TITLE_LEVELS if pattern.search(title or "")]
    for m in _NUMBERED_LEVEL.finditer(title or ""):
        levels.append(_NUMBER_LEVELS[m.group(1).lower()])
    return levels


def assess(job: Job) -> ExperienceReq:
    levels = title_levels(job.title)
    found = mentions(job.description or "")
    if found:
        multi_level = len({name for name, _, _ in levels}) > 1
        pick = min if multi_level else max
        lo, hi, snippet = pick(found, key=lambda f: f[0])
        return ExperienceReq(min_years=lo, max_years=hi, basis="description", evidence=snippet)
    if levels:
        name, lo, hi = min(levels, key=lambda lv: lv[1])
        return ExperienceReq(min_years=lo, max_years=hi, basis="title", evidence=f"title level: {name}")
    return ExperienceReq()
