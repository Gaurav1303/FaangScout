import httpx
import yaml

from faangscout.companies.registry import CompanyRegistry, RegistryEntry, load_registry
from faangscout.discover import (
    discover,
    load_candidates,
    plan_attempts,
    slug_candidates,
    to_registry_yaml,
    workday_candidates,
)
from faangscout.models import CompanySource


def gh_jobs(*titles):
    return {
        "jobs": [
            {"id": i, "title": t, "absolute_url": f"https://x/{i}", "updated_at": "2026-06-01T00:00:00Z",
             "location": {"name": "Remote"}, "departments": [], "content": ""}
            for i, t in enumerate(titles)
        ]
    }


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestCandidates:
    def test_hand_picked_slugs_come_first_and_dedupe(self):
        slugs = slug_candidates("Compass", ("compass", "urban compass"), ["urbancompass"])
        assert slugs[0] == "urbancompass"
        assert slugs.count("urbancompass") == 1
        assert "compass" in slugs

    def test_punctuated_names_collapse(self):
        assert "deshaw" in slug_candidates("D. E. Shaw")

    def test_workday_listed_host_tried_first_then_defaults(self):
        configs = workday_candidates([{"tenant": "acme", "hosts": ["wd12"], "sites": ["External"]}])
        assert configs[0]["host"] == "acme.wd12.myworkdayjobs.com"
        assert {c["host"] for c in configs} >= {"acme.wd1.myworkdayjobs.com", "acme.wd5.myworkdayjobs.com"}
        assert len({c["host"] for c in configs}) == len(configs)  # wd12 not repeated

    def test_workday_specs_are_tried_before_slugs(self):
        attempts = plan_attempts("Acme", (), {"workday": [{"tenant": "acme", "sites": ["X"]}]})
        assert attempts[0][0] == "workday"
        assert attempts[-1][0] != "workday"

    def test_bundled_candidates_load_and_are_keyed_by_registry_name(self):
        candidates = load_candidates()
        registry = load_registry()
        for key in candidates:
            assert any(key == name.lower().replace(" ", "").replace(".", "")
                       for name in [e.name for e in registry.entries]), key


class TestDiscover:
    def test_finds_greenhouse_board_via_hand_picked_slug(self):
        def handler(request):
            if "/boards/urbancompass/" in str(request.url):
                return httpx.Response(200, json=gh_jobs("Software Engineer", "Designer"))
            return httpx.Response(404)

        reg = CompanyRegistry(entries=[RegistryEntry("Compass", ("compass",), ())])
        [result] = discover(["Compass"], reg, candidates={"compass": {"slugs": ["urbancompass"]}},
                            client=client(handler))
        assert result.found
        assert result.source == CompanySource("greenhouse", {"board": "urbancompass"})
        assert result.sample_titles == ["Software Engineer", "Designer"]

    def test_empty_board_is_not_accepted(self):
        """Some boards answer 200 with no jobs for any token - that's not proof."""
        def handler(request):
            if "greenhouse" in str(request.url):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(404)

        reg = CompanyRegistry(entries=[RegistryEntry("Acme", (), ())])
        [result] = discover(["Acme"], reg, candidates={}, client=client(handler))
        assert not result.found
        assert any(a.outcome == "empty" for a in result.attempts)

    def test_skips_companies_that_already_have_a_board(self):
        reg = CompanyRegistry(entries=[RegistryEntry("Stripe", (), (CompanySource("greenhouse", {"board": "stripe"}),))])
        assert discover(["Stripe"], reg, candidates={}, client=client(lambda r: httpx.Response(500))) == []

    def test_workday_hit(self):
        def handler(request):
            if request.method == "POST" and "acme.wd5.myworkdayjobs.com" in str(request.url):
                return httpx.Response(200, json={"total": 1, "jobPostings": [
                    {"title": "SWE", "externalPath": "/job/1", "postedOn": "Posted Today"}]})
            return httpx.Response(404)

        reg = CompanyRegistry(entries=[RegistryEntry("Acme", (), ())])
        spec = {"acme": {"workday": [{"tenant": "acme", "sites": ["External"]}]}}
        [result] = discover(["Acme"], reg, candidates=spec, client=client(handler))
        assert result.source.provider == "workday"
        assert result.source.config["host"] == "acme.wd5.myworkdayjobs.com"

    def test_duplicate_names_probe_once(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(404)

        reg = CompanyRegistry(entries=[RegistryEntry("Rubrik", ("rubrik", "rubirik"), ())])
        results = discover(["Rubrik", "Rubirik"], reg, candidates={}, client=client(handler))
        assert len(results) == 1

    def test_registry_yaml_round_trips_through_load_registry(self, tmp_path):
        def handler(request):
            if "/boards/acme/" in str(request.url):
                return httpx.Response(200, json=gh_jobs("Engineer"))
            return httpx.Response(404)

        reg = CompanyRegistry(entries=[RegistryEntry("Acme", ("acme",), ())])
        results = discover(["Acme"], reg, candidates={}, client=client(handler))
        path = tmp_path / "discovered.yaml"
        path.write_text(to_registry_yaml(results))

        assert yaml.safe_load(path.read_text())["companies"][0]["name"] == "Acme"
        merged = load_registry(path)
        assert merged.lookup("acme").sources[0].config == {"board": "acme"}


def test_excluded_boards_are_never_tried():
    from faangscout.discover import plan_attempts

    attempts = plan_attempts("LinkedIn", ("linkedin",), {"exclude": ["greenhouse:linkedin"]})
    assert ("greenhouse", {"board": "linkedin"}) not in attempts
    assert ("lever", {"site": "linkedin"}) in attempts
