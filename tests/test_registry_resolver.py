import httpx
import pytest

from faangscout.companies.registry import CompanyRegistry, RegistryEntry, load_registry
from faangscout.companies.resolver import resolve_companies
from faangscout.models import CompanySource


def entry(name, aliases=(), sources=()):
    return RegistryEntry(name=name, aliases=aliases, sources=sources)


class TestRegistry:
    def test_exact_and_alias_lookup(self):
        reg = CompanyRegistry(entries=[entry("Stripe", aliases=("stripe",))])
        assert reg.lookup("Stripe").name == "Stripe"
        assert reg.lookup("stripe").name == "Stripe"
        assert reg.lookup("STRIPE").name == "Stripe"

    def test_unknown_returns_none(self):
        reg = CompanyRegistry(entries=[entry("Stripe")])
        assert reg.lookup("totally-unknown-co") is None

    def test_fuzzy_typo_match(self):
        reg = CompanyRegistry(entries=[entry("Stripe", aliases=("stripe",))])
        assert reg.lookup("Stirpe") is not None

    def test_merge_overrides_same_name(self):
        base = CompanyRegistry(entries=[entry("Acme", sources=(CompanySource("greenhouse", {"board": "old"}),))])
        overrides = CompanyRegistry(entries=[entry("Acme", sources=(CompanySource("greenhouse", {"board": "new"}),))])
        base.merge(overrides)
        assert base.lookup("Acme").sources[0].config["board"] == "new"

    def test_suggest_returns_close_names(self):
        reg = CompanyRegistry(entries=[entry("Stripe", aliases=("stripe",)), entry("Square", aliases=("square",))])
        suggestions = reg.suggest("strp")
        assert "Stripe" in suggestions

    def test_bundled_registry_loads(self):
        reg = load_registry()
        assert len(reg.entries) > 0
        assert reg.lookup("stripe") is not None

    def test_overrides_file(self, tmp_path):
        overrides = tmp_path / "overrides.yaml"
        overrides.write_text(
            "companies:\n"
            "  - name: MyStartup\n"
            "    aliases: [mystartup]\n"
            "    sources:\n"
            "      - provider: greenhouse\n"
            "        config: {board: mystartup}\n"
        )
        reg = load_registry(overrides)
        assert reg.lookup("mystartup") is not None
        assert reg.lookup("stripe") is not None  # bundled seed still present


class TestResolver:
    def test_registry_hit(self):
        reg = CompanyRegistry(entries=[entry("Stripe", aliases=("stripe",), sources=(CompanySource("greenhouse", {"board": "stripe"}),))])
        resolved = resolve_companies(["stripe"], reg)
        assert len(resolved) == 1
        assert resolved[0].resolved is True
        assert resolved[0].origin == "registry"

    def test_unresolved_when_not_found(self):
        reg = CompanyRegistry(entries=[])
        resolved = resolve_companies(["totally-unknown-co"], reg)
        assert resolved[0].resolved is False
        assert resolved[0].origin == "unresolved"

    def test_unresolved_when_registry_entry_has_no_sources(self):
        reg = CompanyRegistry(entries=[entry("Meta", aliases=("meta",), sources=())])
        resolved = resolve_companies(["Meta"], reg)
        assert resolved[0].resolved is False

    def test_inline_override_bypasses_registry(self):
        reg = CompanyRegistry(entries=[])
        resolved = resolve_companies(["Acme:greenhouse:board=acme"], reg)
        assert len(resolved) == 1
        assert resolved[0].resolved is True
        assert resolved[0].sources[0].provider == "greenhouse"
        assert resolved[0].sources[0].config["board"] == "acme"
        assert resolved[0].origin == "inline"

    def test_inline_with_multiple_kv_pairs(self):
        reg = CompanyRegistry(entries=[])
        resolved = resolve_companies(["Acme:workday:host=acme.wd1.myworkdayjobs.com,tenant=acme,site=External"], reg)
        cfg = resolved[0].sources[0].config
        assert cfg["host"] == "acme.wd1.myworkdayjobs.com"
        assert cfg["tenant"] == "acme"
        assert cfg["site"] == "External"

    def test_one_result_per_query_preserves_order(self):
        reg = CompanyRegistry(entries=[entry("Stripe", aliases=("stripe",), sources=(CompanySource("greenhouse", {"board": "stripe"}),))])
        resolved = resolve_companies(["stripe", "unknown-1", "stripe", "unknown-2"], reg)
        assert [r.query for r in resolved] == ["stripe", "unknown-1", "stripe", "unknown-2"]

    def test_probe_success(self):
        reg = CompanyRegistry(entries=[])

        def handler(request):
            if "greenhouse" in str(request.url):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        resolved = resolve_companies(["someco"], reg, probe=True, client=client)
        assert resolved[0].resolved is True
        assert resolved[0].origin == "discovered"
        assert resolved[0].sources[0].provider == "greenhouse"

    def test_probe_failure_stays_unresolved(self):
        reg = CompanyRegistry(entries=[])
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        resolved = resolve_companies(["someco"], reg, probe=True, client=client)
        assert resolved[0].resolved is False
