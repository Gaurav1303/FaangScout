from datetime import UTC, datetime, timedelta

import httpx
import pytest

from faangscout.models import FetchHints, Precision
from faangscout.providers.amazon import AmazonProvider
from faangscout.providers.ashby import AshbyProvider
from faangscout.providers.eightfold import EightfoldProvider
from faangscout.providers.base import ProviderError
from faangscout.providers.greenhouse import GreenhouseProvider
from faangscout.providers.lever import LeverProvider
from faangscout.providers.oracle_hcm import OracleHcmProvider
from faangscout.providers.smartrecruiters import SmartRecruitersProvider
from faangscout.providers.workday import WorkdayProvider

HINTS = FetchHints()


def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestGreenhouse:
    def test_happy_path(self):
        def handler(request):
            assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 42,
                            "title": "Backend Engineer",
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                            "updated_at": "2026-06-01T10:00:00Z",
                            "location": {"name": "Remote - US"},
                            "departments": [{"name": "Engineering"}],
                            "content": "<p>Build things &amp; ship.</p>",
                        }
                    ]
                },
            )

        provider = GreenhouseProvider(client=client_with(handler))
        jobs = provider.fetch({"board": "acme", "company_name": "Acme"}, HINTS)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.company == "Acme"
        assert job.title == "Backend Engineer"
        assert job.source == "greenhouse"
        assert job.precision == Precision.APPROXIMATE
        assert job.posted_at == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        assert job.locations == ("Remote - US",)
        assert job.remote is True
        assert job.department == "Engineering"
        assert "Build things & ship." in job.description

    def test_missing_board_raises(self):
        provider = GreenhouseProvider(client=client_with(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)

    def test_http_error_raises_provider_error(self):
        provider = GreenhouseProvider(client=client_with(lambda r: httpx.Response(404)))
        with pytest.raises(ProviderError):
            provider.fetch({"board": "nope"}, HINTS)

    def test_bad_json_raises_provider_error(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        provider = GreenhouseProvider(client=client_with(handler))
        with pytest.raises(ProviderError):
            provider.fetch({"board": "acme"}, HINTS)


class TestLever:
    def test_happy_path(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "abc123",
                        "text": "Senior Frontend Engineer",
                        "hostedUrl": "https://jobs.lever.co/acme/abc123",
                        "createdAt": 1780000000000,
                        "categories": {"location": "New York", "team": "Web", "commitment": "Full-time"},
                        "descriptionPlain": "Join our web team.",
                        "workplaceType": "hybrid",
                    }
                ],
            )

        provider = LeverProvider(client=client_with(handler))
        jobs = provider.fetch({"site": "acme", "company_name": "Acme"}, HINTS)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Senior Frontend Engineer"
        assert job.precision == Precision.EXACT
        assert job.locations == ("New York",)
        assert job.department == "Web"
        assert job.employment_type == "Full-time"

    def test_unexpected_shape_raises(self):
        provider = LeverProvider(client=client_with(lambda r: httpx.Response(200, json={"not": "a list"})))
        with pytest.raises(ProviderError):
            provider.fetch({"site": "acme"}, HINTS)


class TestAshby:
    def test_happy_path(self):
        def handler(request):
            assert request.method == "GET"
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "xyz",
                            "title": "Platform Engineer",
                            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
                            "publishedAt": "2026-06-01T00:00:00.000Z",
                            "location": "Remote",
                            "isRemote": True,
                            "department": "Platform",
                            "employmentType": "FullTime",
                            "descriptionPlain": "Work on infra.",
                        }
                    ]
                },
            )

        provider = AshbyProvider(client=client_with(handler))
        jobs = provider.fetch({"board": "acme", "company_name": "Acme"}, HINTS)

        assert len(jobs) == 1
        assert jobs[0].remote is True
        assert jobs[0].precision == Precision.EXACT

    def test_missing_board_raises(self):
        provider = AshbyProvider(client=client_with(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)


class TestSmartRecruiters:
    def test_pagination(self):
        calls = []

        def handler(request):
            offset = int(request.url.params.get("offset", 0))
            calls.append(offset)
            if offset == 0:
                content = [{"id": str(i), "name": f"Role {i}", "location": {}, "releasedDate": "2026-06-01T00:00:00Z"} for i in range(100)]
                return httpx.Response(200, json={"content": content, "totalFound": 120})
            content = [{"id": str(i), "name": f"Role {i}", "location": {}, "releasedDate": "2026-06-01T00:00:00Z"} for i in range(100, 120)]
            return httpx.Response(200, json={"content": content, "totalFound": 120})

        provider = SmartRecruitersProvider(client=client_with(handler))
        jobs = provider.fetch({"company": "acme", "company_name": "Acme"}, FetchHints(max_results=1000))

        assert len(jobs) == 120
        assert calls == [0, 100]


class TestWorkday:
    def test_happy_path_and_pagination_stop(self):
        def handler(request):
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Site Reliability Engineer",
                            "externalPath": "/job/SRE/1",
                            "postedOn": "Posted Today",
                            "locationsText": "Seattle, WA",
                        }
                    ],
                },
            )

        provider = WorkdayProvider(client=client_with(handler))
        jobs = provider.fetch(
            {"host": "acme.wd1.myworkdayjobs.com", "tenant": "acme", "site": "External", "company_name": "Acme"},
            HINTS,
        )

        assert len(jobs) == 1
        job = jobs[0]
        assert job.url == "https://acme.wd1.myworkdayjobs.com/External/job/SRE/1"
        assert job.precision == Precision.DATE_ONLY
        assert (job.posted_at.hour, job.posted_at.minute) == (0, 0)  # a day, not "<1h ago"
        assert job.locations == ("Seattle, WA",)

    def test_missing_config_raises(self):
        provider = WorkdayProvider(client=client_with(lambda r: httpx.Response(200, json={})))
        with pytest.raises(ProviderError):
            provider.fetch({"tenant": "acme"}, HINTS)


class TestAmazon:
    """amazon.jobs search.json - in-house portal, date-only timestamps."""

    def _payload(self, n=1, hits=1):
        return {
            "error": None,
            "hits": hits,
            "jobs": [
                {
                    "id_icims": f"28473{i}",
                    "title": "Software Development Engineer II",
                    "job_path": f"/en/jobs/28473{i}/software-development-engineer-ii",
                    "posted_date": "March 5, 2026",
                    "normalized_location": "Seattle, Washington, USA",
                    "location": "US, WA, Seattle",
                    "job_category": "Software Development",
                    "job_schedule_type": "Full Time",
                    "description": "<p>Build large scale systems.</p>",
                }
                for i in range(n)
            ],
        }

    def test_happy_path(self):
        provider = AmazonProvider(client=client_with(lambda r: httpx.Response(200, json=self._payload())))
        jobs = provider.fetch({"company_name": "Amazon"}, HINTS)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.company == "Amazon"
        assert job.title == "Software Development Engineer II"
        assert job.url == "https://www.amazon.jobs/en/jobs/284730/software-development-engineer-ii"
        assert job.source == "amazon"
        assert job.external_id == "284730"
        assert job.precision == Precision.DATE_ONLY
        assert job.posted_at == datetime(2026, 3, 5, tzinfo=UTC)
        assert job.locations == ("Seattle, Washington, USA",)
        assert job.department == "Software Development"
        assert "Build large scale systems." in job.description

    def test_role_hint_is_sent_as_base_query(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=self._payload())

        provider = AmazonProvider(client=client_with(handler))
        provider.fetch({}, FetchHints(role_query="backend engineer"))
        assert seen["base_query"] == "backend engineer"
        assert seen["sort"] == "recent"

    def test_api_error_field_raises(self):
        provider = AmazonProvider(
            client=client_with(lambda r: httpx.Response(200, json={"error": "rate limited", "jobs": []}))
        )
        with pytest.raises(ProviderError, match="rate limited"):
            provider.fetch({}, HINTS)

    def test_shape_change_raises_rather_than_returning_empty(self):
        provider = AmazonProvider(
            client=client_with(lambda r: httpx.Response(200, json={"results": []}))
        )
        with pytest.raises(ProviderError, match="no 'jobs' key"):
            provider.fetch({}, HINTS)

    def test_http_error_raises(self):
        provider = AmazonProvider(client=client_with(lambda r: httpx.Response(403)))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)


class TestEightfold:
    """PCSX search, shape copied from a live apply.careers.microsoft.com response."""

    LIVE_POSITION = {
        "id": 1970393556944735, "displayJobId": "200045438",
        "name": "Principal Software Engineering Manager - GitHub (CoreAI)",
        "locations": ["United States, California, Mountain View"],
        "standardizedLocations": ["Mountain View, CA, US"],
        "postedTs": 1790129489, "department": "Software Engineering",
        "creationTs": 1784926896, "workLocationOption": "onsite",
        "positionUrl": "/careers/job/1970393556944735",
    }
    CONFIG = {"host": "apply.careers.microsoft.com", "domain": "microsoft.com", "company_name": "Microsoft"}

    def _payload(self, positions, count=None):
        data = {"positions": positions}
        if count is not None:
            data["count"] = count
        return {"status": 200, "error": {"message": "", "body": ""}, "data": data}

    def test_live_shape(self):
        provider = EightfoldProvider(client=client_with(
            lambda r: httpx.Response(200, json=self._payload([self.LIVE_POSITION], count=1))))
        [job] = provider.fetch(self.CONFIG, HINTS)
        assert job.company == "Microsoft"
        assert job.url == "https://apply.careers.microsoft.com/careers/job/1970393556944735"
        assert job.external_id == "200045438"
        assert job.posted_at == datetime.fromtimestamp(1790129489, tz=UTC)  # postedTs, not creationTs
        assert job.locations == ("Mountain View, CA, US",)
        assert job.remote is False
        assert job.department == "Software Engineering"

    def test_sends_domain_query_and_sort(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=self._payload([], count=0))

        EightfoldProvider(client=client_with(handler)).fetch(self.CONFIG, FetchHints(role_query="software engineer"))
        assert request_path(seen) == {"domain": "microsoft.com", "query": "software engineer", "sort_by": "timestamp"}

    def test_pages_until_empty(self):
        starts = []

        def handler(request):
            start = int(request.url.params["start"])
            starts.append(start)
            batch = [dict(self.LIVE_POSITION, id=start + i) for i in range(10)] if start < 20 else []
            return httpx.Response(200, json=self._payload(batch))

        jobs = EightfoldProvider(client=client_with(handler)).fetch(self.CONFIG, FetchHints(max_results=1000))
        assert starts == [0, 10, 20]
        assert len(jobs) == 20

    def test_stops_when_a_page_predates_the_window(self):
        starts = []

        def handler(request):
            starts.append(int(request.url.params["start"]))
            return httpx.Response(200, json=self._payload([dict(self.LIVE_POSITION, postedTs=1_000_000_000)] * 10))

        since = datetime(2026, 9, 1, tzinfo=UTC)
        EightfoldProvider(client=client_with(handler)).fetch(self.CONFIG, FetchHints(since=since))
        assert starts == [0]

    def test_legacy_endpoint_error_is_explicit(self):
        """What the retired /api/apply/v2 path returns - must not read as 'no jobs'."""
        provider = EightfoldProvider(client=client_with(
            lambda r: httpx.Response(200, json={"message": "Not authorized for PCSX"})))
        with pytest.raises(ProviderError, match="Not authorized for PCSX"):
            provider.fetch(self.CONFIG, HINTS)

    def test_requires_host_and_domain(self):
        with pytest.raises(ProviderError):
            EightfoldProvider(client=client_with(lambda r: httpx.Response(200))).fetch({"host": "x"}, HINTS)


def request_path(params):
    return {k: params[k] for k in ("domain", "query", "sort_by")}


class TestOracleHcm:
    """recruitingCEJobRequisitions - shape from Oracle's API, not yet seen live."""

    CONFIG = {"host": "jpmc.fa.oraclecloud.com", "site": "CX_1001", "company_name": "JPMorgan Chase"}

    def _payload(self, reqs, total=None):
        item = {"requisitionList": reqs}
        if total is not None:
            item["TotalJobsCount"] = total
        return {"items": [item], "count": 1, "hasMore": False}

    def _req(self, i=1, **kw):
        return {"Id": str(210000000 + i), "Title": "Software Engineer III", "PostedDate": "2026-09-22",
                "PrimaryLocation": "Bengaluru, Karnataka, India",
                "secondaryLocations": [{"Name": "Mumbai, Maharashtra, India"}], **kw}

    def test_parses_requisition(self):
        provider = OracleHcmProvider(client=client_with(lambda r: httpx.Response(200, json=self._payload([self._req()], 1))))
        [job] = provider.fetch(self.CONFIG, HINTS)
        assert job.title == "Software Engineer III"
        assert job.url == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210000001"
        assert job.precision == Precision.DATE_ONLY
        assert job.posted_at == datetime(2026, 9, 22, tzinfo=UTC)
        assert job.locations == ("Bengaluru, Karnataka, India", "Mumbai, Maharashtra, India")

    def test_finder_carries_site_sort_and_keyword(self):
        seen = {}

        def handler(request):
            seen["finder"] = request.url.params["finder"]
            return httpx.Response(200, json=self._payload([], 0))

        OracleHcmProvider(client=client_with(handler)).fetch(self.CONFIG, FetchHints(role_query="software engineer"))
        assert seen["finder"].startswith("findReqs;siteNumber=CX_1001,")
        assert "sortBy=POSTING_DATES_DESC" in seen["finder"]
        assert 'keyword="software engineer"' in seen["finder"]

    def test_empty_items_is_zero_jobs(self):
        provider = OracleHcmProvider(client=client_with(lambda r: httpx.Response(200, json={"items": []})))
        assert provider.fetch(self.CONFIG, HINTS) == []

    def test_unexpected_shape_raises(self):
        provider = OracleHcmProvider(client=client_with(lambda r: httpx.Response(200, json={"foo": 1})))
        with pytest.raises(ProviderError, match="no 'items'"):
            provider.fetch(self.CONFIG, HINTS)


class TestGreenhouseDates:
    def _entry(self, **extra):
        return {"id": 1, "title": "SWE", "absolute_url": "u", "updated_at": "2026-09-22T20:00:00Z",
                "location": {"name": "Remote"}, "departments": [], "content": "", **extra}

    def test_prefers_first_published_over_bulk_refreshed_updated_at(self):
        payload = {"jobs": [self._entry(first_published="2026-05-01T10:00:00Z")]}
        provider = GreenhouseProvider(client=client_with(lambda r: httpx.Response(200, json=payload)))
        [job] = provider.fetch({"board": "acme"}, HINTS)
        assert job.posted_at == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        assert job.precision == Precision.EXACT

    def test_falls_back_to_updated_at(self):
        payload = {"jobs": [self._entry()]}
        provider = GreenhouseProvider(client=client_with(lambda r: httpx.Response(200, json=payload)))
        [job] = provider.fetch({"board": "acme"}, HINTS)
        assert job.posted_at == datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
        assert job.precision == Precision.APPROXIMATE


class TestRetries:
    """A transient 429/503 must not drop a whole company from the report."""

    def _provider(self, responses, sleeps):
        calls = iter(responses)

        def handler(request):
            item = next(calls)
            if isinstance(item, Exception):
                raise item
            return item

        provider = GreenhouseProvider(client=client_with(handler))
        provider._sleep = sleeps.append
        return provider

    def test_retries_429_then_succeeds_honouring_retry_after(self):
        sleeps = []
        provider = self._provider(
            [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={"jobs": []})], sleeps)
        assert provider.fetch({"board": "acme"}, HINTS) == []
        assert sleeps == [3.0]

    def test_gives_up_after_three_attempts(self):
        sleeps = []
        provider = self._provider([httpx.Response(503)] * 3, sleeps)
        with pytest.raises(ProviderError, match="HTTP 503"):
            provider.fetch({"board": "acme"}, HINTS)
        assert sleeps == [2.0, 6.0]

    def test_retry_after_is_capped(self):
        sleeps = []
        provider = self._provider(
            [httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, json={"jobs": []})], sleeps)
        provider.fetch({"board": "acme"}, HINTS)
        assert sleeps == [30.0]

    def test_timeout_is_retried(self):
        sleeps = []
        provider = self._provider([httpx.ReadTimeout("slow"), httpx.Response(200, json={"jobs": []})], sleeps)
        assert provider.fetch({"board": "acme"}, HINTS) == []
        assert sleeps == [2.0]

    def test_404_is_not_retried(self):
        sleeps = []
        provider = self._provider([httpx.Response(404)], sleeps)
        with pytest.raises(ProviderError, match="HTTP 404"):
            provider.fetch({"board": "acme"}, HINTS)
        assert sleeps == []

    def test_workday_post_goes_through_retry(self):
        sleeps = []
        calls = iter([httpx.Response(429), httpx.Response(200, json={"total": 0, "jobPostings": []})])
        provider = WorkdayProvider(client=client_with(lambda r: next(calls)))
        provider._sleep = sleeps.append
        provider.fetch({"host": "a.wd1.myworkdayjobs.com", "tenant": "a", "site": "X"}, HINTS)
        assert sleeps == [2.0]


class TestWorkdayOrdering:
    """Keyword search is relevance-ordered on Workday - fetch the date-ordered list instead."""

    def _posting(self, i, posted):
        return {"title": f"Software Engineer {i}", "externalPath": f"/job/X/{i}", "postedOn": posted}

    def test_never_sends_search_text(self):
        bodies = []

        def handler(request):
            import json as _json
            bodies.append(_json.loads(request.content))
            return httpx.Response(200, json={"total": 0, "jobPostings": []})

        WorkdayProvider(client=client_with(handler)).fetch(
            {"host": "n.wd5.myworkdayjobs.com", "tenant": "n", "site": "S"}, FetchHints(role_query="software engineer"))
        assert bodies[0]["searchText"] == ""

    def test_stops_paging_once_a_page_predates_the_window(self):
        offsets = []

        def handler(request):
            import json as _json
            offset = _json.loads(request.content)["offset"]
            offsets.append(offset)
            posted = "Posted Today" if offset == 0 else "Posted 30+ Days Ago"
            return httpx.Response(200, json={"total": 2000, "jobPostings": [self._posting(offset + i, posted) for i in range(20)]})

        since = datetime.now(UTC) - timedelta(hours=24)
        jobs = WorkdayProvider(client=client_with(handler)).fetch(
            {"host": "n.wd5.myworkdayjobs.com", "tenant": "n", "site": "S"}, FetchHints(since=since, max_results=400))
        assert offsets == [0, 20]  # page 2 is all old -> stop, not 20 pages
        assert len(jobs) == 40

    def test_yesterday_page_does_not_stop_paging(self):
        """'Posted Yesterday' is inside a 24h window under the day-only grace."""
        offsets = []

        def handler(request):
            import json as _json
            offset = _json.loads(request.content)["offset"]
            offsets.append(offset)
            posted = {0: "Posted Today", 20: "Posted Yesterday"}.get(offset, "Posted 30+ Days Ago")
            return httpx.Response(200, json={"total": 2000, "jobPostings": [self._posting(offset + i, posted) for i in range(20)]})

        since = datetime.now(UTC) - timedelta(hours=24)
        WorkdayProvider(client=client_with(handler)).fetch(
            {"host": "n.wd5.myworkdayjobs.com", "tenant": "n", "site": "S"}, FetchHints(since=since))
        assert offsets == [0, 20, 40]
