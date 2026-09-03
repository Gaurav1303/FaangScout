from datetime import UTC, datetime

import httpx
import pytest

from faangscout.models import FetchHints, Precision
from faangscout.providers.ashby import AshbyProvider
from faangscout.providers.base import ProviderError
from faangscout.providers.greenhouse import GreenhouseProvider
from faangscout.providers.lever import LeverProvider
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
