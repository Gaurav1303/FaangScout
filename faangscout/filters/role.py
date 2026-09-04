"""Keyword-based role matching.

Expands the user's role string into synonyms/phrases via
``normalize.expand_role_terms`` and matches against title (weighted highest),
then department and description. This is the default, always-available role
filter - no API key needed. ``SemanticRoleFilter`` (in ``semantic.py``) is an
optional, higher-recall replacement for ambiguous or oddly-worded role
queries, gated behind an Anthropic API key.
"""

from __future__ import annotations

import re

from ..models import Job, Rejection, SearchCriteria
from ..normalize import expand_role_query, normalize_title
from .base import Filter, register


@register("role")
class RoleKeywordFilter(Filter):
    def enabled(self, criteria: SearchCriteria) -> bool:
        # When semantic matching is requested, SemanticRoleFilter owns role
        # matching outright instead of refining what this filter already
        # narrowed - keyword matching has false negatives (e.g. "Founding
        # Engineer" for a backend role) that a second pass can't undo.
        return bool(criteria.filters.get("role")) and not criteria.filters.get("semantic")

    def apply(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection]]:
        role = criteria.filters.get("role")
        if not role:
            return jobs, []

        query = expand_role_query(role)
        if query.empty:
            return jobs, []

        # A specialization in the query is mandatory ("backend engineer" must
        # not match "Frontend Engineer"); with no specialization, the family
        # terms decide.
        deciding_terms = query.required or query.optional
        if not deciding_terms:
            return jobs, []

        patterns = [_compile(t) for t in deciding_terms]

        kept: list[Job] = []
        rejected: list[Rejection] = []
        for job in jobs:
            title_norm = normalize_title(job.title)
            haystacks = (job.title, title_norm, job.department or "")
            if any(p.search(h) for p in patterns for h in haystacks if h):
                kept.append(job)
            else:
                rejected.append(Rejection(job, self.name, f"title {job.title!r} does not match role {role!r}"))
        return kept, rejected


def _compile(term: str) -> re.Pattern[str]:
    """Word-boundary match, tolerating hyphen/space variants ("back-end"/"back end")."""
    pattern = r"[\s\-/]+".join(re.escape(part) for part in re.split(r"[\s\-/]+", term) if part)
    return re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", re.IGNORECASE)
