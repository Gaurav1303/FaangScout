"""Optional semantic role matching via the Anthropic API.

Keyword matching (``role.py``) is fast and needs no API key, but it misses
titles that don't share vocabulary with the query - "Founding Engineer" for
a "backend engineer" search, or "Member of Technical Staff" for "software
engineer". This filter asks Claude to judge fit directly instead.

Jobs are scored in batches (default 40 titles per call, one JSON-schema
response) rather than one request per job - a 24h window across a dozen
companies is typically a few hundred postings, so batching is the difference
between ~10 calls and ~300.

Enabled by setting ``criteria.filters["semantic"]`` to ``True`` (or a dict
overriding ``chunk_size``/``threshold``/``model``) *and* the role query being
present. If the Anthropic SDK isn't installed or no credentials resolve, this
filter fails soft: it logs a warning (surfaced in ``ScoutReport.warnings``)
and falls back to plain keyword matching rather than dropping every job or
crashing the run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..models import Job, Rejection, SearchCriteria
from .base import Filter, register
from .role import RoleKeywordFilter

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_CHUNK_SIZE = 40
DEFAULT_THRESHOLD = 0.55

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "match": {"type": "boolean"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "match", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You screen job postings for a candidate's desired role. For each posting, "
    "decide whether the ROLE it's hiring for is a reasonable match for the "
    "candidate's target role - judge by responsibilities and seniority a "
    "title like this typically implies, not surface keyword overlap. "
    "Score 0.0-1.0: 1.0 is a clear match, 0.0 is clearly unrelated. "
    "Set match=true only when score would be >= 0.5. Keep 'reason' to one "
    "short clause."
)


@dataclass(frozen=True, slots=True)
class SemanticConfig:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    threshold: float = DEFAULT_THRESHOLD
    model: str = DEFAULT_MODEL

    @classmethod
    def from_criteria(cls, criteria: SearchCriteria) -> "SemanticConfig":
        raw = criteria.filters.get("semantic")
        if not isinstance(raw, dict):
            return cls()
        return cls(
            chunk_size=int(raw.get("chunk_size", DEFAULT_CHUNK_SIZE)),
            threshold=float(raw.get("threshold", DEFAULT_THRESHOLD)),
            model=str(raw.get("model", DEFAULT_MODEL)),
        )


@register("semantic")
class SemanticRoleFilter(Filter):
    order = 25
    """Role filter keyed on ``semantic`` in ``criteria.filters``.

    Injectable client for testing: pass an object exposing
    ``.messages.create(...)`` compatible with the Anthropic SDK response
    shape to ``SemanticRoleFilter(client=...)``.
    """

    def __init__(self, client: object | None = None) -> None:
        self._client = client

    def enabled(self, criteria: SearchCriteria) -> bool:
        return bool(criteria.filters.get("role")) and bool(criteria.filters.get("semantic"))

    def apply(
        self, jobs: list[Job], criteria: SearchCriteria
    ) -> tuple[list[Job], list[Rejection], list[str]]:
        role = criteria.filters["role"]
        if not jobs:
            return jobs, [], []

        client = self._client or self._build_client()
        if client is None:
            warning = "semantic role matching requested but the Anthropic SDK/credentials are unavailable; fell back to keyword matching"
            logger.warning(warning)
            kept, rejected = RoleKeywordFilter().apply(jobs, criteria)
            return kept, rejected, [warning]

        config = SemanticConfig.from_criteria(criteria)
        kept: list[Job] = []
        rejected: list[Rejection] = []
        warnings: list[str] = []

        for start in range(0, len(jobs), config.chunk_size):
            batch = jobs[start : start + config.chunk_size]
            try:
                scores = self._score_batch(client, role, batch, config)
            except Exception as exc:  # noqa: BLE001 - any SDK/network failure
                warning = f"semantic scoring failed for {len(batch)} job(s) ({exc!r}); fell back to keyword matching"
                logger.warning(warning)
                warnings.append(warning)
                sub_kept, sub_rejected = RoleKeywordFilter().apply(batch, criteria)
                kept.extend(sub_kept)
                rejected.extend(sub_rejected)
                continue

            for i, job in enumerate(batch):
                result = scores.get(i)
                if result is None:
                    rejected.append(Rejection(job, self.name, "no score returned by model"))
                    continue
                if result["match"] and result["score"] >= config.threshold:
                    kept.append(job)
                else:
                    rejected.append(
                        Rejection(job, self.name, f"score {result['score']:.2f}: {result['reason']}")
                    )

        return kept, rejected, warnings

    @staticmethod
    def _build_client() -> object | None:
        try:
            import anthropic
        except ImportError:
            return None
        try:
            return anthropic.Anthropic()
        except Exception:  # noqa: BLE001 - no resolvable credentials, etc.
            return None

    @staticmethod
    def _score_batch(client, role: str, batch: list[Job], config: SemanticConfig) -> dict[int, dict]:
        listing = "\n".join(
            f"{i}. title={job.title!r} department={job.department!r} location={job.location_text!r}"
            for i, job in enumerate(batch)
        )
        user_message = f"Candidate's target role: {role!r}\n\nPostings:\n{listing}"

        response = client.messages.create(
            model=config.model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}},
        )

        text = next(b.text for b in response.content if b.type == "text")
        payload = json.loads(text)
        return {int(r["id"]): r for r in payload["results"]}
