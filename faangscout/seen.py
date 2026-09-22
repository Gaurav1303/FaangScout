"""Remember which postings have already been reported.

A daily run with a 24h window mostly sees new postings anyway, but not
entirely: date-only sources get a 24h grace (see ``Precision.DATE_ONLY``), and
a run that lands a little early or late overlaps the previous one. Without
this, the same job would be emailed twice.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Job

#: Forget entries after this long, so the file doesn't grow forever. Anything
#: older is far outside any time window a search would use.
RETENTION = timedelta(days=30)


def job_key(job: Job) -> str:
    if job.external_id:
        return f"{job.source}:{job.external_id}"
    return job.url


class SeenStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._seen: dict[str, str] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text() or "{}")
                self._seen = dict(data.get("seen", {}))
            except (ValueError, AttributeError):
                # A corrupt file costs at most one repeated email; don't fail the run.
                self._seen = {}

    def __contains__(self, job: Job) -> bool:
        return job_key(job) in self._seen

    def filter_new(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if j not in self]

    def mark(self, jobs: list[Job], *, now: datetime | None = None) -> None:
        stamp = (now or datetime.now(UTC)).isoformat()
        for job in jobs:
            self._seen.setdefault(job_key(job), stamp)

    def save(self, *, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(UTC)) - RETENTION
        kept = {
            key: stamp
            for key, stamp in self._seen.items()
            if datetime.fromisoformat(stamp) >= cutoff
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": kept}, indent=1, sort_keys=True))
