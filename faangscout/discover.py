"""Find the job board behind a company that has no confirmed one yet.

Most employers publish through a handful of ATS vendors, each of which has a
predictable public endpoint keyed by a board token. The token is usually the
company name, but not always (Compass is ``urbancompass``), and Workday adds a
numbered host that cannot be derived at all. Discovery tries a short list of
candidates per company and keeps the first one that returns real postings.

Every probe goes through the same provider class a normal search uses, so a
hit means the code that will fetch this company's jobs has already parsed a
live response from it - not merely that a URL returned 200.

Candidates come from two places: slugs derived from the registry name and
aliases, and hand-picked extras in ``companies/data/discovery_candidates.yaml``.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import resources

import httpx
import yaml

from .companies.registry import CompanyRegistry
from .models import CompanySource, FetchHints
from .normalize import collapse, slugify
from .providers.base import ProviderError, get_provider

#: ATS providers probed by slug, and the config key each one's token lives under.
SLUG_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("greenhouse", "board"),
    ("lever", "site"),
    ("ashby", "board"),
    ("smartrecruiters", "company"),
)

#: Workday host numbers tried after any listed in a candidate's own `hosts`.
DEFAULT_WORKDAY_HOSTS: tuple[str, ...] = ("wd1", "wd3", "wd5", "wd12", "wd103")

_MAX_DERIVED_SLUGS = 4
_SAMPLE_TITLES = 3


@dataclass
class Attempt:
    provider: str
    config: dict
    outcome: str  # "hit" | "empty" | "error"
    detail: str = ""


@dataclass
class DiscoveryResult:
    company: str
    aliases: tuple[str, ...] = ()
    source: CompanySource | None = None
    total_jobs: int = 0
    sample_titles: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def found(self) -> bool:
        return self.source is not None


def load_candidates() -> dict[str, dict]:
    text = resources.files("faangscout.companies.data").joinpath("discovery_candidates.yaml").read_text()
    data = yaml.safe_load(text) or {}
    return {collapse(name): spec or {} for name, spec in (data.get("candidates") or {}).items()}


def slug_candidates(name: str, aliases: tuple[str, ...] = (), extra: list[str] | None = None) -> list[str]:
    """Hand-picked slugs first, then ones derived from the name and aliases."""
    ordered: list[str] = list(extra or [])
    derived: list[str] = []
    for value in (name, *aliases):
        for slug in (collapse(value), slugify(value)):
            if slug and slug not in derived:
                derived.append(slug)
    ordered.extend(derived[:_MAX_DERIVED_SLUGS])

    seen: set[str] = set()
    return [s for s in ordered if not (s in seen or seen.add(s))]


def workday_candidates(specs: list[dict]) -> list[dict]:
    """Expand each ``{tenant, sites, hosts?}`` spec into concrete provider configs."""
    configs: list[dict] = []
    for spec in specs or []:
        tenant = spec.get("tenant")
        if not tenant:
            continue
        hosts = list(spec.get("hosts") or [])
        hosts += [h for h in DEFAULT_WORKDAY_HOSTS if h not in hosts]
        for host in hosts:
            for site in spec.get("sites") or []:
                configs.append(
                    {"host": f"{tenant}.{host}.myworkdayjobs.com", "tenant": tenant, "site": site}
                )
    return configs


def plan_attempts(name: str, aliases: tuple[str, ...], spec: dict) -> list[tuple[str, dict]]:
    """Ordered (provider, config) pairs to try for one company.

    A company with Workday candidates listed is almost certainly on Workday,
    so those go first; otherwise the cheap ATS slug probes lead.
    """
    slug_attempts = [
        (provider, {key: slug})
        for slug in slug_candidates(name, aliases, spec.get("slugs"))
        for provider, key in SLUG_PROVIDERS
    ]
    workday_attempts = [("workday", cfg) for cfg in workday_candidates(spec.get("workday"))]
    attempts = workday_attempts + slug_attempts if workday_attempts else slug_attempts
    # Known false positives: a same-named but unrelated (or test) board.
    excluded = {str(e).lower() for e in spec.get("exclude") or []}
    return [(p, c) for p, c in attempts
            if not any(f"{p}:{v}".lower() in excluded for v in c.values() if isinstance(v, str))]


def discover_one(
    name: str, aliases: tuple[str, ...], spec: dict, client: httpx.Client
) -> DiscoveryResult:
    result = DiscoveryResult(company=name, aliases=aliases)
    started = time.monotonic()
    hints = FetchHints(max_results=1)

    for provider_name, config in plan_attempts(name, aliases, spec):
        try:
            provider = get_provider(provider_name, client=client)
            jobs = provider.fetch({**config, "company_name": name}, hints)
        except ProviderError as exc:
            result.attempts.append(Attempt(provider_name, config, "error", str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad probe must not end discovery
            result.attempts.append(Attempt(provider_name, config, "error", f"unexpected: {exc!r}"))
            continue

        if not jobs:
            # A clean-but-empty answer is too weak to trust: some boards
            # return an empty list for any token at all.
            result.attempts.append(Attempt(provider_name, config, "empty", "0 postings"))
            continue

        result.attempts.append(Attempt(provider_name, config, "hit", f"{len(jobs)} postings"))
        result.source = CompanySource(provider=provider_name, config=config)
        result.total_jobs = len(jobs)
        result.sample_titles = [j.title for j in jobs[:_SAMPLE_TITLES]]
        break

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def discover(
    companies: list[str],
    registry: CompanyRegistry,
    *,
    candidates: dict[str, dict] | None = None,
    client: httpx.Client | None = None,
    max_workers: int = 8,
) -> list[DiscoveryResult]:
    """Probe every company in ``companies`` that the registry can't already fetch."""
    candidates = load_candidates() if candidates is None else candidates

    todo: list[tuple[str, tuple[str, ...], dict]] = []
    seen: set[str] = set()
    for query in companies:
        entry = registry.lookup(query)
        if entry is not None and entry.resolved:
            continue
        name = entry.name if entry else query.strip()
        if collapse(name) in seen:
            continue
        seen.add(collapse(name))
        aliases = entry.aliases if entry else ()
        todo.append((name, aliases, candidates.get(collapse(name), {})))

    if not todo:
        return []

    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": "FaangScout/0.1", "Accept": "application/json"},
    )
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(lambda t: discover_one(t[0], t[1], t[2], client), todo))
    finally:
        if owns_client:
            client.close()


def to_registry_yaml(results: list[DiscoveryResult]) -> str:
    """Hits as a registry overrides file (loadable via ``load_registry(path)``)."""
    entries = [
        {
            "name": r.company,
            "aliases": list(r.aliases),
            "sources": [{"provider": r.source.provider, "config": dict(r.source.config)}],
        }
        for r in results
        if r.found
    ]
    header = (
        "# Written by `faangscout --discover`. Each entry is a board that answered\n"
        "# with real postings - check the sample titles in the run log before\n"
        "# copying one into known_boards.yaml.\n"
    )
    return header + yaml.safe_dump({"companies": entries}, sort_keys=False)
