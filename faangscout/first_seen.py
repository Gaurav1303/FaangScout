"""Dates for boards that publish none: when did FaangScout first see a job?

Some boards (Jobvite, Rippling's ATS) list open jobs with no posting date,
so "posted in the last 24h" can't be read from the board. For those,
``posted_at`` becomes the time the posting first appeared in a run - a new
job appears in the next daily run after it's posted.

The first time a board is tracked, everything already on it is recorded
with no date, so the first run doesn't report a board's entire backlog as
"new". Only postings that appear after that get a first-seen date.

Needs to persist between runs (the workflow keeps it in the same cache as
the seen-jobs file). Without a store, these jobs have no date and the time
window drops them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Job, Precision
from .seen import job_key

#: Postings not seen for this long are forgotten (closed long ago).
RETENTION = timedelta(days=60)
_BACKLOG = "backlog"


class FirstSeenStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # {board: {"tracked_since": iso, "jobs": {key: [first_seen_iso | "backlog", last_seen_iso]}}}
        self.boards: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.boards = dict(json.loads(self.path.read_text() or "{}").get("boards", {}))
            except (ValueError, AttributeError):
                self.boards = {}

    def date(self, jobs: list[Job], *, now: datetime | None = None) -> list[Job]:
        """Give FIRST_SEEN jobs their first-seen date (or None for the backlog)."""
        now = now or datetime.now(UTC)
        stamp = now.isoformat()
        out: list[Job] = []
        new_boards: set[str] = set()
        for job in jobs:
            if job.precision is not Precision.FIRST_SEEN:
                out.append(job)
                continue
            board_key = f"{job.company}|{job.source}"
            board = self.boards.get(board_key)
            if board is None:
                board = self.boards[board_key] = {"tracked_since": stamp, "jobs": {}}
                new_boards.add(board_key)
            key = job_key(job)
            entry = board["jobs"].get(key)
            if entry is None:
                first = _BACKLOG if board_key in new_boards else stamp
                entry = board["jobs"][key] = [first, stamp]
            entry[1] = stamp
            first_seen = None if entry[0] == _BACKLOG else datetime.fromisoformat(entry[0])
            out.append(replace(job, posted_at=first_seen))
        return out

    def save(self, *, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(UTC)) - RETENTION
        for board in self.boards.values():
            board["jobs"] = {
                k: v for k, v in board["jobs"].items() if datetime.fromisoformat(v[1]) >= cutoff
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"boards": self.boards}, indent=1, sort_keys=True))
