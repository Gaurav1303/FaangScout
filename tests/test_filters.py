from datetime import UTC, datetime, timedelta

from faangscout.filters import FilterPipeline
from faangscout.filters.role import RoleKeywordFilter
from faangscout.filters.semantic import SemanticRoleFilter
from faangscout.filters.time_window import TimeWindowFilter
from faangscout.models import Job, Rejection, SearchCriteria

NOW = datetime.now(UTC)


def make_job(title="Software Engineer", *, hours_ago=1.0, posted_at="unset", **kw):
    if posted_at == "unset":
        posted_at = NOW - timedelta(hours=hours_ago)
    return Job(company="Acme", title=title, url="https://x", source="test", posted_at=posted_at, **kw)


class TestTimeWindow:
    def test_keeps_within_window(self):
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        jobs = [make_job(hours_ago=1), make_job(hours_ago=23.9)]
        kept, rejected = TimeWindowFilter().apply(jobs, criteria)
        assert len(kept) == 2
        assert rejected == []

    def test_drops_outside_window(self):
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        jobs = [make_job(hours_ago=25)]
        kept, rejected = TimeWindowFilter().apply(jobs, criteria)
        assert kept == []
        assert len(rejected) == 1

    def test_drops_undated_by_default(self):
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24)
        jobs = [make_job(posted_at=None)]
        kept, rejected = TimeWindowFilter().apply(jobs, criteria)
        assert kept == []
        assert rejected[0].reason == "no parseable post date"

    def test_keeps_undated_when_included(self):
        criteria = SearchCriteria.build(["acme"], posted_within_hours=24, include_undated=True)
        jobs = [make_job(posted_at=None)]
        kept, rejected = TimeWindowFilter().apply(jobs, criteria)
        assert len(kept) == 1
        assert rejected == []

    def test_no_window_keeps_everything(self):
        criteria = SearchCriteria.build(["acme"], posted_within_hours=None)
        jobs = [make_job(hours_ago=1000)]
        kept, rejected = TimeWindowFilter().apply(jobs, criteria)
        assert len(kept) == 1

    def test_always_enabled(self):
        criteria = SearchCriteria(companies=["acme"])
        assert TimeWindowFilter().enabled(criteria) is True


class TestRoleKeyword:
    def test_matches_synonym(self):
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None)
        jobs = [make_job("Back End Software Developer"), make_job("Product Designer")]
        kept, rejected = RoleKeywordFilter().apply(jobs, criteria)
        assert len(kept) == 1
        assert kept[0].title == "Back End Software Developer"

    def test_no_role_passes_through(self):
        criteria = SearchCriteria.build(["acme"], role=None, posted_within_hours=None)
        jobs = [make_job("Anything")]
        kept, rejected = RoleKeywordFilter().apply(jobs, criteria)
        assert kept == jobs

    def test_disabled_when_semantic_requested(self):
        criteria = SearchCriteria.build(["acme"], role="backend", posted_within_hours=None, semantic=True)
        assert RoleKeywordFilter().enabled(criteria) is False

    def test_enabled_without_semantic(self):
        criteria = SearchCriteria.build(["acme"], role="backend", posted_within_hours=None)
        assert RoleKeywordFilter().enabled(criteria) is True


class _FakeContent:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


class TestSemanticRoleFilter:
    def test_scores_and_filters_by_threshold(self):
        import json

        payload = json.dumps(
            {
                "results": [
                    {"id": 0, "match": True, "score": 0.9, "reason": "clear fit"},
                    {"id": 1, "match": False, "score": 0.1, "reason": "unrelated"},
                ]
            }
        )
        client = _FakeClient(payload)
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None, semantic=True)
        jobs = [make_job("Founding Engineer"), make_job("Marketing Manager")]

        kept, rejected, warnings = SemanticRoleFilter(client=client).apply(jobs, criteria)

        assert warnings == []
        assert len(kept) == 1
        assert kept[0].title == "Founding Engineer"
        assert len(rejected) == 1
        assert client.messages.calls[0]["model"] == "claude-opus-5"

    def test_falls_back_on_missing_credentials(self, monkeypatch):
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None, semantic=True)
        jobs = [make_job("Backend Engineer"), make_job("Sales Rep")]

        filt = SemanticRoleFilter(client=None)
        monkeypatch.setattr(filt, "_build_client", staticmethod(lambda: None))

        kept, rejected, warnings = filt.apply(jobs, criteria)
        assert len(warnings) == 1
        assert "fell back to keyword matching" in warnings[0]
        assert len(kept) == 1
        assert kept[0].title == "Backend Engineer"

    def test_falls_back_on_api_error(self):
        class _BrokenMessages:
            def create(self, **kwargs):
                raise RuntimeError("rate limited")

        class _BrokenClient:
            messages = _BrokenMessages()

        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None, semantic=True)
        jobs = [make_job("Backend Engineer")]

        kept, rejected, warnings = SemanticRoleFilter(client=_BrokenClient()).apply(jobs, criteria)
        assert len(warnings) == 1
        assert "semantic scoring failed" in warnings[0]
        assert len(kept) == 1  # fell back to keyword match, which matches

    def test_not_enabled_without_flag(self):
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None)
        assert SemanticRoleFilter().enabled(criteria) is False

    def test_empty_jobs_short_circuits(self):
        criteria = SearchCriteria.build(["acme"], role="backend engineer", posted_within_hours=None, semantic=True)
        kept, rejected, warnings = SemanticRoleFilter(client=_FakeClient("{}")).apply([], criteria)
        assert kept == [] and rejected == [] and warnings == []


class TestFilterPipeline:
    def test_time_and_role_compose(self):
        criteria = SearchCriteria.build(["acme"], role="backend", posted_within_hours=24)
        jobs = [
            make_job("Backend Engineer", hours_ago=1),
            make_job("Backend Engineer", hours_ago=48),
            make_job("Designer", hours_ago=1),
        ]
        kept, rejected, warnings = FilterPipeline().run(jobs, criteria)
        assert len(kept) == 1
        assert kept[0].title == "Backend Engineer"
        assert len(rejected) == 2
