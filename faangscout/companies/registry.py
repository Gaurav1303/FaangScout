"""Loads and queries the company -> board registry.

The registry is plain data (YAML), not code, so adding a company never means
touching Python. Two files are merged in order:

1. The bundled seed list (``companies/data/known_boards.yaml``).
2. An optional user overrides file, path taken from the
   ``FAANGSCOUT_COMPANIES_FILE`` env var or passed explicitly. Entries there
   with the same name replace the bundled one - handy for fixing a stale
   token or adding a company the seed list doesn't know about, without
   forking the package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from importlib import resources
from pathlib import Path

import yaml

from ..models import CompanySource
from ..normalize import collapse

_ENV_OVERRIDES = "FAANGSCOUT_COMPANIES_FILE"
_FUZZY_THRESHOLD = 0.82


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    name: str
    aliases: tuple[str, ...]
    sources: tuple[CompanySource, ...]

    @property
    def resolved(self) -> bool:
        return bool(self.sources)


@dataclass
class CompanyRegistry:
    entries: list[RegistryEntry] = field(default_factory=list)
    _by_key: dict[str, RegistryEntry] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        self._by_key = {}
        for entry in self.entries:
            for key in self._keys_for(entry):
                self._by_key[key] = entry

    @staticmethod
    def _keys_for(entry: RegistryEntry) -> set[str]:
        keys = {collapse(entry.name)}
        keys.update(collapse(alias) for alias in entry.aliases)
        return keys

    def merge(self, other: "CompanyRegistry") -> None:
        """Overlay ``other`` on top of self; same-name entries are replaced."""
        by_name = {collapse(e.name): e for e in self.entries}
        for entry in other.entries:
            by_name[collapse(entry.name)] = entry
        self.entries = list(by_name.values())
        self._reindex()

    def lookup(self, query: str) -> RegistryEntry | None:
        """Exact/alias match, then a fuzzy fallback for typos and near-misses."""
        key = collapse(query)
        if not key:
            return None
        if key in self._by_key:
            return self._by_key[key]

        best: tuple[float, RegistryEntry | None] = (0.0, None)
        for candidate_key, entry in self._by_key.items():
            ratio = SequenceMatcher(None, key, candidate_key).ratio()
            if ratio > best[0]:
                best = (ratio, entry)
        if best[0] >= _FUZZY_THRESHOLD:
            return best[1]
        return None

    def suggest(self, query: str, limit: int = 3) -> list[str]:
        key = collapse(query)
        scored = sorted(
            self._by_key.items(),
            key=lambda kv: SequenceMatcher(None, key, kv[0]).ratio(),
            reverse=True,
        )
        seen: set[str] = set()
        out: list[str] = []
        for _, entry in scored:
            if entry.name in seen:
                continue
            seen.add(entry.name)
            out.append(entry.name)
            if len(out) >= limit:
                break
        return out


def _entry_from_dict(data: dict) -> RegistryEntry:
    sources = tuple(
        CompanySource(provider=s["provider"], config=dict(s.get("config", {})))
        for s in data.get("sources", []) or []
    )
    return RegistryEntry(
        name=data["name"],
        aliases=tuple(data.get("aliases", [])),
        sources=sources,
    )


def _load_yaml_text(text: str) -> CompanyRegistry:
    data = yaml.safe_load(text) or {}
    entries = [_entry_from_dict(d) for d in data.get("companies", [])]
    return CompanyRegistry(entries=entries)


def load_seed_registry() -> CompanyRegistry:
    text = resources.files("faangscout.companies.data").joinpath("known_boards.yaml").read_text()
    return _load_yaml_text(text)


def load_registry(overrides_path: str | Path | None = None) -> CompanyRegistry:
    """Bundled seed + optional overrides file (explicit arg, then env var)."""
    registry = load_seed_registry()

    path = overrides_path or os.environ.get(_ENV_OVERRIDES)
    if path:
        path = Path(path)
        if path.exists():
            registry.merge(_load_yaml_text(path.read_text()))
    return registry
