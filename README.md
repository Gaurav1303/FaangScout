# FaangScout

An agent that watches company career pages for openings matching your desired
role, posted within a recent time window (last 24h, last 2 days, or any
custom window), and hands you the direct links.

You give it a list of company names and a role. It resolves each company to
its underlying job-board API (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday), fetches every open posting, filters to what was posted recently and
matches your role, and returns direct links to each posting.

## Design

The pipeline is four independently-swappable layers:

```
company names  ->  [registry/resolver]  ->  [providers]  ->  [filters]  ->  results
   (yours)          name -> board(s)         board -> jobs    criteria
```

- **`companies/`** - maps a company name to one or more job-board sources.
  Bundled as data (`companies/data/known_boards.yaml`), not code - adding a
  company means adding a YAML entry, never touching Python. See "Adding a
  company" below.
- **`providers/`** - one class per ATS backend (Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday), each implementing the same `fetch(config,
  hints) -> list[Job]` interface. Adding a new job-board backend means adding
  one file and registering it - nothing else changes. See "Adding a
  provider".
- **`filters/`** - a pluggable pipeline keyed by `SearchCriteria.filters`.
  Ships with a time-window filter and a keyword role filter; an optional
  semantic role filter (Claude-based) can replace the keyword one. See
  "Adding a filter".
- **`scout.py`** - the orchestrator that wires the three together: resolves
  companies, fetches every board concurrently (one bad board never blocks the
  others), dedupes, runs the filter pipeline, and returns a `ScoutReport`
  with results plus a clear account of what failed or couldn't be resolved.

Two front ends sit on top of the same `scout()` call: a CLI (`faangscout`)
and a small FastAPI backend + static HTML page.

## Install

```bash
pip install -e .
# or: pip install -r requirements.txt
```

Python 3.11+.

## CLI usage

```bash
# last 24h (default), keyword role match
faangscout --companies Stripe Airbnb Coinbase --role "backend engineer"

# last 2 days
faangscout --companies Stripe Airbnb --role "backend engineer" --hours 48

# JSON output, for piping into something else
faangscout --companies Stripe --role "ml engineer" --json

# see why jobs were dropped
faangscout --companies Stripe --role "ml engineer" --explain

# a company not in the bundled registry, given inline
faangscout --companies "Acme Corp:greenhouse:board=acme" --role backend

# a company not in the registry - try guessing its board
faangscout --companies SomeStartup --role backend --probe
```

Run `faangscout --help` for the full flag list.

## Web UI

```bash
uvicorn faangscout.api.app:app --reload
```

Then open `http://127.0.0.1:8000` - a one-page form (companies, role, time
window, a couple of checkboxes) that calls `POST /api/search` and lists
results. It's intentionally minimal; the interesting logic is all in
`scout()`, not the UI.

## Try it offline (no internet needed)

`demo/fixture_board.py` serves Greenhouse-, Lever-, and Ashby-shaped JSON on
localhost, and `demo/companies.yaml` points three demo companies at it via
each provider's `base_url` config key. The whole pipeline runs for real -
only the boards are local:

```bash
python demo/fixture_board.py --port 8765 &

faangscout --companies-file demo/companies.yaml \
  --companies "Demo Greenhouse Co" "Demo Lever Co" "Demo Ashby Co" \
  --role "backend engineer" --hours 24

# or point the web UI at the same fixtures
FAANGSCOUT_COMPANIES_FILE=demo/companies.yaml uvicorn faangscout.api.app:app
```

Postings are generated relative to "now", so `--hours 24` vs `--hours 48`
visibly changes the result set. The same `base_url` key works for real
self-hosted or proxied boards.

## Adding a company

Two ways, no code changes either way:

1. **Edit the bundled seed file**, `faangscout/companies/data/known_boards.yaml`:
   ```yaml
   - name: Acme Corp
     aliases: [acme, acmecorp]
     sources:
       - provider: greenhouse
         config: {board: acme}
   ```
2. **Or keep your own overrides file** and point `FAANGSCOUT_COMPANIES_FILE`
   (or `--companies-file`) at it - same YAML shape. Entries there override
   same-named entries in the bundled file, so you can fix a stale token
   without forking the package.

To find a company's board: visit its careers page and look at the URL of the
underlying board - `boards.greenhouse.io/{token}`, `jobs.lever.co/{token}`,
`jobs.ashbyhq.com/{token}`, or a Workday `{tenant}.wdN.myworkdayjobs.com/...`
URL (Workday needs `host`, `tenant`, and `site` - see
`faangscout/providers/workday.py` for the exact mapping).

For a one-off company you don't want to add anywhere, use the inline form on
the CLI: `"Acme Corp:greenhouse:board=acme"`.

Every HTTP provider also accepts an optional `base_url` in its config,
overriding the default API host - used by the offline demo above, and useful
for a proxied or self-hosted board.

### Accuracy of the seed registry

The bundled list was written from training knowledge, not verified against
live board tokens (see "A note on this build" below - this environment
couldn't reach the job-board APIs to check). A stale or wrong token doesn't
fail silently: it shows up as a `SourceReport` error for that company (in the
CLI's "Source errors" section, or `sources[].error` in JSON/API output), never
as "this company has no jobs." Treat the seed list as a starting point to
verify against real board URLs, not a guarantee.

True FAANG-scale in-house career sites - Google, Meta, Apple, Amazon
corporate, Netflix - run custom career platforms with no public ATS API this
tool can call, so they're listed with empty `sources` (resolved but
unreachable) rather than guessed at.

## Adding a provider

Add `faangscout/providers/my_ats.py`:

```python
from .base import Provider, ProviderError, register
from ..models import FetchHints, Job

@register("my_ats")
class MyAtsProvider(Provider):
    def fetch(self, config: dict, hints: FetchHints) -> list[Job]:
        ...  # call the board's API, return normalized Job objects
```

Then import it in `faangscout/providers/__init__.py`. It's immediately usable
by name (`provider: my_ats` in a company's YAML config, or
`Name:my_ats:key=value` inline).

## Adding a filter

Add `faangscout/filters/my_filter.py`:

```python
from .base import Filter, register
from ..models import Job, Rejection, SearchCriteria

@register("my_filter")
class MyFilter(Filter):
    def apply(self, jobs: list[Job], criteria: SearchCriteria):
        ...  # return (kept, rejected)
```

Import it in `faangscout/filters/__init__.py`. It runs automatically once
`criteria.filters["my_filter"]` is set (e.g. via
`SearchCriteria.build(..., my_filter=...)` or a CLI/API flag you add) - see
`filters/base.py:Filter.enabled` to change when it activates.

## Optional: semantic role matching

Keyword matching (`role.py`) is fast, needs no API key, and expands your
role into common synonyms (`"backend engineer"` also matches `"back end
developer"`, `"SDE"`, etc. - see `normalize.ROLE_SYNONYMS`). It still misses
titles that share no vocabulary with the query, like "Founding Engineer" for
a backend search.

Passing `--semantic` switches role matching to Claude, which judges fit from
responsibilities and seniority rather than surface keywords. Titles are
scored in batches (default 40 per API call, not one call per job) via a
single JSON-schema-constrained request per batch.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
pip install anthropic   # or: pip install -e ".[semantic]"
faangscout --companies Stripe --role "backend engineer" --semantic
```

If the SDK isn't installed or no credentials resolve, this fails soft: it
logs a warning (surfaced in the report) and falls back to keyword matching
rather than dropping every result.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

All 86 tests run against mocked HTTP responses (`httpx.MockTransport`) - no
network access needed, and none of the numbers in these tests came from a
live board.

## A note on this build

This project was built in a network-restricted sandbox that only allows
egress to PyPI, npm, GitHub, and the Anthropic API - the real job-board APIs
(Greenhouse, Lever, Ashby, SmartRecruiters, Workday) were unreachable, so
nothing here was tested against a live board. Everything was instead
validated two ways: unit tests against mocked HTTP responses shaped exactly
like each API's real (documented) response format, and a live run of the CLI
and the FastAPI server (via `uvicorn` + `curl`) against the "unresolved
company" and "inline override" code paths, which don't require reaching an
external board.

Before relying on this for a real job search:
- Spot-check a handful of registry entries against the company's actual
  careers page - board tokens do drift when a company migrates ATS.
- Run `faangscout --companies <name> --role <role> --explain` once per new
  company and check `sources[].error` (CLI: "Source errors") is empty.
