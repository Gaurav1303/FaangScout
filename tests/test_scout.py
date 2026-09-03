from datetime import UTC, datetime, timedelta

import httpx

from faangscout.companies.registry import CompanyRegistry, RegistryEntry
from faangscout.models import CompanySource, SearchCriteria
from faangscout.scout import scout

NOW = datetime.now(UTC)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _patch_httpx_client(monkeypatch, handler):
    """Force every httpx.Client constructed during scout() to use a mock transport."""
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("faangscout.scout.httpx.Client", fake_client)
    monkeypatch.setattr("faangscout.providers.base.httpx.Client", fake_client)


class TestScout:
    def test_full_pipeline_filters_and_dedupes(self, monkeypatch):
        payload = {
            "jobs": [
                {
                    "id": 1,
                    "title": "Backend Engineer",
                    "absolute_url": "https://x/1",
                    "updated_at": _iso(NOW - timedelta(hours=2)),
                    "location": {"name": "Remote"},
                    "departments": [],
                    "content": "",
                },
                {
                    "id": 2,
                    "title": "Backend Engineer",
                    "absolute_url": "https://x/2",
                    "updated_at": _iso(NOW - timedelta(hours=100)),
                    "location": {"name": "SF"},
                    "departments": [],
                    "content": "",
                },
                {
                    "id": 3,
                    "title": "Recruiter",
                    "absolute_url": "https://x/3",
                    "updated_at": _iso(NOW - timedelta(hours=1)),
                    "location": {"name": "NY"},
                    "departments": [],
                    "content": "",
                },
            ]
        }
        _patch_httpx_client(monkeypatch, lambda r: httpx.Response(200, json=payload))

        reg = CompanyRegistry(
            entries=[RegistryEntry(name="Acme", aliases=("acme",), sources=(CompanySource("greenhouse", {"board": "acme"}),))]
        )
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=24)
        report = scout(criteria, registry=reg)

        assert len(report.jobs) == 1
        assert report.jobs[0].job.title == "Backend Engineer"
        assert report.sources[0].fetched == 3
        assert report.sources[0].error is None
        assert report.unresolved == []

    def test_provider_error_is_isolated_per_source(self, monkeypatch):
        def handler(request):
            if "goodco" in str(request.url):
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(500)

        _patch_httpx_client(monkeypatch, handler)

        reg = CompanyRegistry(
            entries=[
                RegistryEntry(name="GoodCo", aliases=("goodco",), sources=(CompanySource("greenhouse", {"board": "goodco"}),)),
                RegistryEntry(name="BadCo", aliases=("badco",), sources=(CompanySource("greenhouse", {"board": "badco"}),)),
            ]
        )
        criteria = SearchCriteria.build(["goodco", "badco"], posted_within_hours=24)
        report = scout(criteria, registry=reg)

        errored = [s for s in report.sources if s.error]
        ok = [s for s in report.sources if not s.error]
        assert len(errored) == 1
        assert len(ok) == 1
        assert errored[0].company == "BadCo"

    def test_unresolved_companies_reported(self):
        reg = CompanyRegistry(entries=[])
        criteria = SearchCriteria.build(["totally-unknown"], posted_within_hours=24)
        report = scout(criteria, registry=reg)
        assert report.unresolved == ["totally-unknown"]
        assert report.jobs == []
        assert any("could not resolve" in w for w in report.warnings)

    def test_no_sources_at_all_short_circuits(self):
        reg = CompanyRegistry(entries=[RegistryEntry(name="Meta", aliases=("meta",), sources=())])
        criteria = SearchCriteria.build(["meta"], posted_within_hours=24)
        report = scout(criteria, registry=reg)
        assert report.jobs == []
        assert report.sources == []

    def test_results_sorted_most_recent_first(self, monkeypatch):
        payload = {
            "jobs": [
                {"id": 1, "title": "A", "absolute_url": "u1", "updated_at": _iso(NOW - timedelta(hours=10)), "location": {}, "departments": [], "content": ""},
                {"id": 2, "title": "B", "absolute_url": "u2", "updated_at": _iso(NOW - timedelta(hours=1)), "location": {}, "departments": [], "content": ""},
                {"id": 3, "title": "C", "absolute_url": "u3", "updated_at": _iso(NOW - timedelta(hours=5)), "location": {}, "departments": [], "content": ""},
            ]
        }
        _patch_httpx_client(monkeypatch, lambda r: httpx.Response(200, json=payload))

        reg = CompanyRegistry(entries=[RegistryEntry(name="Acme", aliases=("acme",), sources=(CompanySource("greenhouse", {"board": "acme"}),))])
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        report = scout(criteria, registry=reg)

        titles = [sj.job.title for sj in report.jobs]
        assert titles == ["B", "C", "A"]

    def test_limit_applies_after_sort(self, monkeypatch):
        payload = {
            "jobs": [
                {"id": i, "title": f"Job {i}", "absolute_url": f"u{i}", "updated_at": _iso(NOW - timedelta(hours=i)), "location": {}, "departments": [], "content": ""}
                for i in range(1, 6)
            ]
        }
        _patch_httpx_client(monkeypatch, lambda r: httpx.Response(200, json=payload))

        reg = CompanyRegistry(entries=[RegistryEntry(name="Acme", aliases=("acme",), sources=(CompanySource("greenhouse", {"board": "acme"}),))])
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24, limit=2)
        report = scout(criteria, registry=reg)

        assert len(report.jobs) == 2
        assert report.jobs[0].job.title == "Job 1"
        assert report.jobs[1].job.title == "Job 2"

    def test_dedup_does_not_conflate_different_levels(self, monkeypatch):
        payload = {
            "jobs": [
                {"id": 1, "title": "Software Engineer II", "absolute_url": "u1", "updated_at": _iso(NOW - timedelta(hours=1)), "location": {}, "departments": [], "content": ""},
                {"id": 2, "title": "Software Engineer III", "absolute_url": "u2", "updated_at": _iso(NOW - timedelta(hours=1)), "location": {}, "departments": [], "content": ""},
            ]
        }
        _patch_httpx_client(monkeypatch, lambda r: httpx.Response(200, json=payload))

        reg = CompanyRegistry(entries=[RegistryEntry(name="Acme", aliases=("acme",), sources=(CompanySource("greenhouse", {"board": "acme"}),))])
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        report = scout(criteria, registry=reg)

        assert len(report.jobs) == 2

    def test_dedup_collapses_true_duplicate_across_sources(self, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"id": 1, "title": "Backend Engineer", "absolute_url": "u1", "updated_at": _iso(NOW - timedelta(hours=1)), "location": {"name": "Remote"}, "departments": [], "content": ""},
                    ]
                },
            )

        _patch_httpx_client(monkeypatch, handler)
        reg = CompanyRegistry(
            entries=[
                RegistryEntry(
                    name="Acme",
                    aliases=("acme",),
                    sources=(
                        CompanySource("greenhouse", {"board": "acme-old"}),
                        CompanySource("greenhouse", {"board": "acme-new"}),
                    ),
                )
            ]
        )
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        report = scout(criteria, registry=reg)

        assert len(report.jobs) == 1
