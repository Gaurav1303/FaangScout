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
- **`providers/`** - one class per board backend (Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday, Eightfold, Oracle Recruiting Cloud, and Amazon's
  in-house portal), each implementing the same `fetch(config,
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

## Location and experience filters

```bash
faangscout --config scout.yaml --location India --experience 3
```

(or `location: India` / `experience: 3` in `scout.yaml`)

**`--location`** keeps jobs in a country. Boards write locations every which
way ("Pune, India", "Hyderabad, TS, IN", "Bengaluru, Karnataka, IND", or just
"Noida"), so it matches the country name, its ISO codes, and its major cities
and states. An ISO code only counts as the *last* part of a location -
"Indianapolis, IN, US" is Indiana, not India. A job with several locations
passes if any one is in the country.

**`--experience N`** keeps jobs someone with N years qualifies for: the
posting's required range must contain N. For 3: "2+ yrs" and "3-5 yrs" pass;
"5+ yrs", "Senior"/"Lead", and fresher "0-2 yrs" roles don't. The requirement
is read from the description:

- the strictest minimum among the requirements wins ("5+ years of
  development, 3+ with Kafka" needs 5) - unless the title is open at two
  levels ("Software Engineer 2 / Senior"), where the lower one counts;
- "Preferred" / "Nice to have" sections and lines saying "preferred" or
  "a plus" are ignored;
- a higher-degree route ("... OR Master's Degree AND 1+ year") is cut off
  where it starts, keeping the bachelor's requirement before it, so a
  **bachelor's degree is assumed**.

When the description states nothing, the title's level gives a rough range.
It first checks the company's own ladder, since companies name the same level
differently:

| Company title | SDE level |
|---|---|
| Salesforce MTS, Walmart Software Engineer III, Adobe MTS-2, Nutanix/Cohesity MTS-3 | SDE-2 |
| Sprinklr Senior Product Engineer, Qualcomm Senior Engineer, Mastercard Senior Software Engineer | SDE-2 |

Those ladders are in `faangscout/companies/data/levels.yaml`, taken from
LeetCode Discuss and levels.fyi. For companies not listed, it falls back to a
generic reading ("Engineer II" ~2-6 yrs, "Senior"/"Lead" 5+, "Staff" 8+).

Years stated in the posting always decide. When none are stated, a title at
the wanted SDE level on its company's ladder also passes: SDE-2 by default,
or set `experience: {years: 3, sde: 2}` in `scout.yaml`. The Experience column
shows the level, for example "~1–4 yrs (Salesforce MTS) · ≈ SDE-2".

With no stated years and no recognisable level, the job is **kept** and
marked "not stated". Dropping it would hide real matches.

To add or correct a company's levels, edit `levels.yaml`. Each level is a
title regex, a name, an SDE number, and a range of years; the first match
wins. No code change is needed.

The daily email ends with a **No match today** line for every company that
had nothing. It names the stage where that company's jobs dropped out: no new
postings, none in India, none fitting 3 yrs (with the years they needed),
already sent, board unreachable, or not covered and why. That way every
company you asked for is accounted for.

Descriptions come free with Greenhouse, Lever, Ashby and Amazon listings.
Workday, Eightfold and Oracle need one extra request per job, so those are
fetched only for jobs that already passed the time, role and location
filters - a few dozen requests, not thousands.

## Run it on GitHub Actions (daily email)

If your own network can reach the career portals, the CLI is all you need.
Otherwise - or to get a daily email without keeping a machine on - let
GitHub run it. `.github/workflows/scout.yml` does, on a GitHub-hosted runner:

1. **Discover** a board for every company in `scout.yaml` that has none
   configured (`--discover`: tries Greenhouse / Lever / Ashby /
   SmartRecruiters slugs and Workday tenant/site/host combinations from
   `companies/data/discovery_candidates.yaml`, keeping the first that returns
   real postings).
2. **Check** every board's reachability (`--check`, logged, never fails the run).
3. **Search** and write a Markdown table to the run summary.
4. **Post only new openings** as a comment on the "FaangScout results" issue,
   @-mentioning you, which triggers GitHub's email notification. Jobs already
   reported are remembered in `.faangscout/seen.json`, carried between runs by
   `actions/cache`, so nothing is emailed twice.

**Set up:**

- Edit `scout.yaml` - your companies, role, and time window.
- Make the repo private if you don't want your search visible: Actions logs and
  the results issue show which companies and role you're targeting.
- The daily run (09:00 IST, `cron: "30 3 * * *"` in UTC) only fires from the
  default branch, so it starts once the workflow is merged to `main`.
- Run on demand: **Actions → FaangScout → Run workflow**, optionally
  overriding the role or hours, or picking `check` / `discover` mode.

Every run uploads `discovered.yaml`, `check.log`, and `results.json` as an
artifact. When discovery finds a board, check its sample titles in the log
(the main risk is a same-named company on the same ATS), then move the entry
into `known_boards.yaml` so later runs skip probing it.

The seen-jobs cache is branch-scoped: jobs reported by on-demand runs on a
feature branch may be emailed once more by the first scheduled run on `main`.

## Checking whether a career portal is reachable

A board that returns zero jobs and a board that is blocked, moved, or
reshaped look identical in a normal run - both produce an empty list.
`--check` separates them:

```bash
faangscout --companies Amazon Microsoft Stripe --role "software engineer" --check
```

```
[OK] Amazon (amazon) - 812ms
    47 posting(s) returned, 47 with a usable date
      - Software Development Engineer II
      ...
[UNREACHABLE] Stripe (greenhouse:stripe) - 210ms
    error: greenhouse: https://boards-api.greenhouse.io/... -> HTTP 404
```

Statuses are `OK`, `REACHABLE (0 jobs)`, `REACHABLE (no usable dates)`,
`UNREACHABLE`, and `UNRESOLVED`. It exits non-zero if anything failed, so it
works as a health check in a cron job. Add `--json` for machine-readable
output.

Run this first whenever a company returns nothing and you expected results -
it distinguishes "genuinely no new postings" from "this board token is stale."

## Supported job boards

| Provider | Used by (in the bundled registry) | Date quality |
|---|---|---|
| `greenhouse` | Stripe, Airbnb, Coinbase, Rubrik, Compass, ... | exact (`first_published`) |
| `lever`, `ashby`, `smartrecruiters` | Palantir, OpenAI, Ramp, ... | exact |
| `workday` | Adobe, Salesforce, NVIDIA, Mastercard, Cohesity, Sprinklr | day only ("Posted Today") |
| `eightfold` | Microsoft, Qualcomm | posted/re-posted time |
| `oracle_hcm` | JPMorgan Chase, DP World, Kotak | day only |
| `amazon` | Amazon | day only |
| `apple_jobs` | Apple (server-rendered search pages) | day only |
| `phonepe_feed` | PhonePe (its careers page's JSON feed) | day only |
| `sharechat` | ShareChat (its careers API) | exact |
| `jobvite` | Nutanix | none - first seen |
| `rippling_ats` | Rippling | none - first seen |
| `talentbrew` | Intuit | none - first seen |

Every one of these was confirmed against the live service from a GitHub
Actions runner (September 2026) - including that the job links in the report
open the right posting.

**Amazon, Eightfold, and Oracle endpoints are the ones those sites' own
frontends call, not documented public APIs.** They carry no stability
guarantee. Microsoft already moved once: its old
`gcsservices.careers.microsoft.com` API was retired for Eightfold. Providers
raise a named error on a shape change rather than returning an empty list
that reads like "no jobs today", so `--check` surfaces a break immediately.

**"Day only" sources** give a calendar date, not a time. Those postings are
`Precision.DATE_ONLY`: displayed as "today"/"yesterday", and the time-window
filter widens by 24h for them rather than dropping this morning's job because
it parsed as "32h ago".

**Greenhouse's `updated_at` is not a posting date.** Recruiters bulk-edit
postings; one live run saw 45 months-old Compass jobs all "updated 9h ago".
The provider uses `first_published` and only falls back to `updated_at`.

Transient failures (HTTP 429/502/503/504, timeouts) are retried with backoff,
honouring `Retry-After`, so one rate-limited request doesn't drop a company
from that day's report.

**"First seen" sources** list open jobs with no date at all. With
`--first-seen-file PATH` (the scheduled workflow keeps one in its cache),
such a posting is dated by the first run that saw it, so it shows up in the
next daily report after it's posted. The first run on a board records its
existing backlog undated, so it isn't all reported as new. Without the file,
these jobs have no date: the time window drops them unless you pass
`--include-undated`.

The Apple, PhonePe, ShareChat, Jobvite, Rippling, and TalentBrew sources were
found by loading each careers page in a browser on a GitHub Actions runner
and noting which request actually carries the job list.

Not covered, and why (checked September 2026):

| Company | Why |
|---|---|
| Indeed, MathWorks, Arcesium, Zepto | Careers site blocks scripted access (bot check). FaangScout doesn't try to get around that. |
| Walmart | Search runs through a versioned GraphQL query that changes with site releases. |
| D. E. Shaw (India) | No job list: the India site takes general applications only. |
| Zomato (Eternal) | No public job board found. |
| Google, Meta, Netflix | Not examined yet. |

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

Companies with no working source (see "Not covered" above) are listed with
empty `sources`, resolved but unreachable, rather than guessed at.

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

All 206 tests run against mocked HTTP responses (`httpx.MockTransport`) - no
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
