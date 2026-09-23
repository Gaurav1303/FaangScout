"""Turns user-typed company names into ``ResolvedCompany`` objects.

Three ways a company can resolve, tried in order:

1. **Registry match** - exact, alias, or fuzzy match against
   :class:`~faangscout.companies.registry.CompanyRegistry`.
2. **Inline override** - the user types ``name:provider:key=value,key=value``
   directly (e.g. ``"Acme:greenhouse:board=acme"``), bypassing the registry
   entirely. Useful for a one-off company that isn't worth adding to a file.
3. **Auto-probe** - if neither above resolves and probing is enabled, guess a
   slug from the company name and check it against each cheap-to-probe
   provider (Greenhouse, Lever, Ashby all resolve with a single GET). First
   provider that returns a real board wins. Off by default since it makes a
   network call per unresolved company; the CLI/API turn it on with
   ``--probe``.

Anything that resolves none of these ways comes back as an unresolved
``ResolvedCompany`` (``sources=()``) so the caller can report it instead of
silently dropping the company.
"""

from __future__ import annotations

import httpx

from ..models import CompanySource, ResolvedCompany
from ..normalize import slugify
from ..providers.base import ProviderError, get_provider
from ..providers.base import FetchHints
from .registry import CompanyRegistry

_PROBE_ORDER = ("greenhouse", "lever", "ashby")
_PROBE_CONFIG_KEY = {"greenhouse": "board", "lever": "site", "ashby": "board"}


def _parse_inline(query: str) -> ResolvedCompany | None:
    """``"Acme:greenhouse:board=acme"`` -> a resolved company with one source."""
    if ":" not in query:
        return None
    parts = query.split(":")
    if len(parts) < 2:
        return None
    name, provider, *rest = parts
    provider = provider.strip().lower()
    if provider not in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters"):
        return None

    config: dict[str, str] = {}
    if rest:
        for pair in rest[0].split(","):
            if "=" not in pair:
                continue
            key, _, value = pair.partition("=")
            config[key.strip()] = value.strip()

    name = name.strip()
    return ResolvedCompany(
        query=query,
        name=name,
        sources=(CompanySource(provider=provider, config={**config, "company_name": name}),),
        origin="inline",
    )


def _probe(name: str, client: httpx.Client) -> ResolvedCompany | None:
    slug = slugify(name)
    if not slug:
        return None
    for provider_name in _PROBE_ORDER:
        config_key = _PROBE_CONFIG_KEY[provider_name]
        config = {config_key: slug, "company_name": name}
        try:
            provider = get_provider(provider_name, client=client)
            jobs = provider.fetch(config, FetchHints(max_results=1))
        except ProviderError:
            continue
        else:
            if jobs or jobs == []:  # a clean response (even zero jobs) counts as a real board
                return ResolvedCompany(
                    query=name,
                    name=name,
                    sources=(CompanySource(provider=provider_name, config=config),),
                    origin="discovered",
                )
    return None


def resolve_companies(
    queries: list[str],
    registry: CompanyRegistry,
    *,
    probe: bool = False,
    client: httpx.Client | None = None,
) -> list[ResolvedCompany]:
    """Resolve every query string to a company + its board source(s).

    Order per query: inline override -> registry -> (optional) live probe.
    Always returns one ``ResolvedCompany`` per input query, resolved or not.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0, follow_redirects=True)
    resolved: list[ResolvedCompany] = []
    try:
        for query in queries:
            inline = _parse_inline(query)
            if inline is not None:
                resolved.append(inline)
                continue

            entry = registry.lookup(query)
            if entry is not None and entry.resolved:
                resolved.append(
                    ResolvedCompany(query=query, name=entry.name, sources=entry.sources, origin="registry")
                )
                continue

            if probe:
                probed = _probe(entry.name if entry else query, client)
                if probed is not None:
                    resolved.append(probed)
                    continue

            resolved.append(
                ResolvedCompany(
                    query=query,
                    name=entry.name if entry else query,
                    sources=(),
                    origin="unresolved",
                )
            )
        return resolved
    finally:
        if owns_client:
            client.close()
