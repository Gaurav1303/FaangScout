"""Keep only jobs posted within the requested time window.

Runs by default (a scout that returns every job ever posted isn't useful),
using ``SearchCriteria.window_hours`` (default 24h - see
``SearchCriteria.build``). A job with no parseable date is dropped unless
``criteria.include_undated`` is set, since silently including it would make
the window meaningless without the user ever choosing that.
"""

from __future__ import annotations

from ..models import Job, Rejection, SearchCriteria
from .base import Filter, register


@register("posted_within")
class TimeWindowFilter(Filter):
    def enabled(self, criteria: SearchCriteria) -> bool:
        # Always on: an unset window still defaults to "last 24h" per
        # SearchCriteria.build, so absence of the key doesn't mean "no limit".
        return True

    def apply(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection]]:
        since = criteria.since
        if since is None:
            return jobs, []

        kept: list[Job] = []
        rejected: list[Rejection] = []
        for job in jobs:
            if job.posted_at is None:
                if criteria.include_undated:
                    kept.append(job)
                else:
                    rejected.append(Rejection(job, self.name, "no parseable post date"))
                continue
            if job.posted_at >= since:
                kept.append(job)
            else:
                age = job.age
                hours = age.total_seconds() / 3600 if age else float("nan")
                rejected.append(Rejection(job, self.name, f"posted {hours:.1f}h ago, outside window"))
        return kept, rejected
