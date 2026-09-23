from datetime import UTC, datetime

import httpx
import pytest

from faangscout.models import FetchHints, Precision
from faangscout.providers.amazon import AmazonProvider
from faangscout.providers.ashby import AshbyProvider
from faangscout.providers.base import ProviderError
from faangscout.providers.greenhouse import GreenhouseProvider
from faangscout.providers.lever import LeverProvider
from faangscout.providers.microsoft import MicrosoftProvider
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
        assert job.url == "https://acme.wd1.myworkdayjobs.com/job/SRE/1"
        assert job.precision == Precision.APPROXIMATE
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


class TestMicrosoft:
    """gcsservices.careers.microsoft.com search API - nested result envelope."""

    def _payload(self, n=1, total=1):
        return {
            "operationResult": {
                "result": {
                    "totalJobs": total,
                    "jobs": [
                        {
                            "jobId": f"18123{i}",
                            "title": "Senior Software Engineer",
                            "postingDate": "2026-03-05T00:00:00+00:00",
                            "properties": {
                                "primaryLocation": "Redmond, Washington, United States",
                                "locations": ["Redmond, Washington, United States"],
                                "workSiteFlexibility": "Up to 100% work from home",
                                "profession": "Software Engineering",
                                "employmentType": "Full-Time",
                            },
                        }
                        for i in range(n)
                    ],
                }
            }
        }

    def test_happy_path(self):
        provider = MicrosoftProvider(client=client_with(lambda r: httpx.Response(200, json=self._payload())))
        jobs = provider.fetch({"company_name": "Microsoft"}, HINTS)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Senior Software Engineer"
        assert job.url == "https://jobs.careers.microsoft.com/global/en/job/181230"
        assert job.source == "microsoft"
        assert job.precision == Precision.EXACT
        assert job.posted_at == datetime(2026, 3, 5, tzinfo=UTC)
        assert job.locations == ("Redmond, Washington, United States",)
        assert job.remote is True  # "work from home"
        assert job.department == "Software Engineering"
        assert job.employment_type == "Full-Time"

    def test_role_hint_sent_as_q(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=self._payload())

        provider = MicrosoftProvider(client=client_with(handler))
        provider.fetch({}, FetchHints(role_query="data scientist"))
        assert seen["q"] == "data scientist"
        assert seen["o"] == "Recent"

    def test_missing_envelope_raises(self):
        provider = MicrosoftProvider(client=client_with(lambda r: httpx.Response(200, json={"jobs": []})))
        with pytest.raises(ProviderError, match="operationResult"):
            provider.fetch({}, HINTS)

    def test_string_location_is_tolerated(self):
        payload = self._payload()
        payload["operationResult"]["result"]["jobs"][0]["properties"]["locations"] = "Dublin, Ireland"
        payload["operationResult"]["result"]["jobs"][0]["properties"].pop("primaryLocation")

        provider = MicrosoftProvider(client=client_with(lambda r: httpx.Response(200, json=payload)))
        jobs = provider.fetch({}, HINTS)
        assert jobs[0].locations == ("Dublin, Ireland",)


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
