"""Filter protocol: one class per criterion, chained into a pipeline.

Every filter takes a list of jobs and the full ``SearchCriteria`` (so it can
read its own config plus see what other filters were asked for) and returns
the subset that passes, plus the rejects it dropped (for ``--explain``). New
filters register themselves with ``@register("name")`` and become usable the
moment ``SearchCriteria.filters`` contains that key - nothing else in the
codebase needs to change to add one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from ..models import Job, Rejection, SearchCriteria

#: Fills in ``Job.description`` for jobs whose listing didn't include one.
Enricher = Callable[[list[Job]], list[Job]]


class Filter(ABC):
    name: str = "base"
    #: Position in the pipeline, lowest first. Cheap, selective filters go
    #: early so expensive ones (see ``needs_description``) see fewer jobs.
    order: int = 50
    #: Set when the filter reads ``Job.description``. The pipeline then asks
    #: its enricher to fetch descriptions for the jobs still in play - and only
    #: those - right before this filter runs.
    needs_description: bool = False

    @abstractmethod
    def apply(
        self, jobs: list[Job], criteria: SearchCriteria
    ) -> tuple[list[Job], list[Rejection]] | tuple[list[Job], list[Rejection], list[str]]:
        """Return (kept, rejected) or (kept, rejected, warnings).

        Must not mutate ``jobs``. The optional third element is for non-fatal
        problems worth surfacing (e.g. "semantic matching unavailable, fell
        back to keyword matching") without failing the whole run.
        """
        raise NotImplementedError

    def enabled(self, criteria: SearchCriteria) -> bool:
        """Whether this filter should run at all for the given criteria.

        Default: run only if the filter's own key is present in
        ``criteria.filters``. Override for a filter that should always run
        (e.g. time window has a default even when unset).
        """
        return self.name in criteria.filters


REGISTRY: dict[str, type[Filter]] = {}


def register(name: str):
    def decorator(cls: type[Filter]) -> type[Filter]:
        cls.name = name
        REGISTRY[name] = cls
        return cls

    return decorator


class FilterPipeline:
    """Runs every registered, enabled filter over a job list, in ``order``."""

    def __init__(self, filters: list[Filter] | None = None, *, enricher: Enricher | None = None) -> None:
        filters = filters if filters is not None else [cls() for cls in REGISTRY.values()]
        self.filters = sorted(filters, key=lambda f: f.order)
        self.enricher = enricher

    def run(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection], list[str]]:
        kept = list(jobs)
        rejections: list[Rejection] = []
        warnings: list[str] = []
        enriched = False
        for filt in self.filters:
            if not filt.enabled(criteria):
                continue
            if filt.needs_description and not enriched and self.enricher is not None and kept:
                kept = self.enricher(kept)
                enriched = True
            result = filt.apply(kept, criteria)
            kept, rejected, *rest = result
            rejections.extend(rejected)
            if rest:
                warnings.extend(rest[0])
        return kept, rejections, warnings
