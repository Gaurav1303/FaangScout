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
from ..normalize import expand_role_terms, normalize_title
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

        terms = expand_role_terms(role)
        if not terms:
            return jobs, []

        patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in terms]

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
