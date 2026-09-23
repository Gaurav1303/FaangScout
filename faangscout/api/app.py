"""FastAPI backend: a thin HTTP wrapper around ``faangscout.scout``.

One endpoint (``POST /api/search``) does the real work; the rest of this
module is request/response shaping and serving the static UI in
``api/static/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..companies.registry import load_registry
from ..models import SearchCriteria
from ..scout import scout

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="FaangScout", description="Career-page job scout")


class SearchRequest(BaseModel):
    companies: list[str] = Field(..., min_length=1, description="Company names to search")
    role: str | None = Field(None, description="Desired role, e.g. 'backend engineer'")
    hours: float = Field(24.0, gt=0, description="Only jobs posted within this many hours")
    include_undated: bool = False
    semantic: bool = Field(False, description="Use Claude for role matching instead of keywords")
    probe: bool = Field(False, description="Try guessing boards for companies not in the registry")
    location: str | None = Field(None, description="Only jobs in this country, e.g. 'India'")
    experience: float | None = Field(None, ge=0, description="Only jobs this many years of experience qualifies for")
    limit: int | None = None


class JobOut(BaseModel):
    company: str
    title: str
    url: str
    source: str
    posted_at: str | None
    precision: str
    locations: list[str]
    remote: bool | None
    experience: str | None = None


class SourceOut(BaseModel):
    company: str
    source: str
    fetched: int
    error: str | None


class SearchResponse(BaseModel):
    jobs: list[JobOut]
    sources: list[SourceOut]
    unresolved: list[str]
    warnings: list[str]


@app.post("/api/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    criteria = SearchCriteria.build(
        request.companies,
        role=request.role,
        posted_within_hours=request.hours,
        include_undated=request.include_undated,
        limit=request.limit,
        semantic=True if request.semantic else None,
        location=request.location,
        experience=request.experience,
    )
    report = scout(criteria, registry=load_registry(), probe_unknown=request.probe)

    return SearchResponse(
        jobs=[
            JobOut(
                company=sj.job.company,
                title=sj.job.title,
                url=sj.job.url,
                source=sj.job.source,
                posted_at=sj.job.posted_at.isoformat() if sj.job.posted_at else None,
                precision=sj.job.precision.value,
                locations=list(sj.job.locations),
                remote=sj.job.remote,
                experience=sj.job.experience.display() if sj.job.experience else None,
            )
            for sj in report.jobs
        ],
        sources=[
            SourceOut(company=s.company, source=s.source, fetched=s.fetched, error=s.error)
            for s in report.sources
        ],
        unresolved=report.unresolved,
        warnings=report.warnings,
    )


@app.get("/api/companies")
def list_companies() -> dict[str, Any]:
    """Companies known to the bundled registry, for the UI's autocomplete."""
    registry = load_registry()
    return {
        "companies": [
            {"name": e.name, "resolved": e.resolved} for e in sorted(registry.entries, key=lambda e: e.name)
        ]
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
