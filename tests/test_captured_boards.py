"""Providers for the boards found by loading careers pages in a browser.

Markup and payloads below are trimmed from real responses captured on
2026-09-23 (GitHub Actions probe runs).
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from faangscout.first_seen import FirstSeenStore
from faangscout.models import FetchHints, Job, Precision
from faangscout.normalize import parse_timestamp
from faangscout.providers.apple import AppleJobsProvider
from faangscout.providers.base import ProviderError
from faangscout.providers.jobvite import JobviteProvider
from faangscout.providers.phonepe import PhonePeFeedProvider
from faangscout.providers.rippling_ats import RipplingAtsProvider
from faangscout.providers.sharechat import ShareChatProvider
from faangscout.providers.talentbrew import TalentBrewProvider

HINTS = FetchHints()


def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestDates:
    def test_dotnet_date(self):
        assert parse_timestamp("/Date(1788134400000)/") == datetime(2026, 8, 31, tzinfo=UTC)

    def test_dotnet_date_with_offset(self):
        assert parse_timestamp("/Date(1788134400000+0530)/") == datetime(2026, 8, 31, tzinfo=UTC)

    def test_sept_abbreviation(self):
        assert parse_timestamp("23 Sept 2026") == datetime(2026, 9, 23, tzinfo=UTC)


class TestPhonePe:
    FEED = {
        "results": [
            {
                "status": "PUBLIC",
                "applyUrl": "https://jobs.smartrecruiters.com/PHONEPELIMITED/1000000000001015-sde-2",
                "type": "Full-time",
                "title": "Software Engineer - Backend",
                "department": "Engineering",
                "location": "Bengaluru",
                "updatedAt": "/Date(1788134400000)/",
            },
            {"status": "NOT_PUBLISHED", "title": "Test Job IJP", "location": "Bengaluru"},
            {"status": "INTERNAL", "title": "Internal Transfer", "location": "Pune"},
        ]
    }

    def test_only_public_postings(self):
        provider = PhonePeFeedProvider(client=client_with(lambda r: httpx.Response(200, json=self.FEED)))
        jobs = provider.fetch({"company_name": "PhonePe"}, HINTS)

        assert [j.title for j in jobs] == ["Software Engineer - Backend"]
        job = jobs[0]
        assert job.url.startswith("https://jobs.smartrecruiters.com/PHONEPELIMITED/")
        assert job.detail_url == job.url
        assert job.locations == ("Bengaluru",)
        assert job.department == "Engineering"
        assert job.posted_at == datetime(2026, 8, 31, tzinfo=UTC)
        assert job.precision == Precision.DATE_ONLY

    def test_details_read_the_posting_page(self):
        def handler(request):
            if "latest.json" in str(request.url):
                return httpx.Response(200, json=self.FEED)
            return httpx.Response(200, text="<html><p>3+ years of experience in Java</p></html>")

        provider = PhonePeFeedProvider(client=client_with(handler))
        job = provider.fetch_details(provider.fetch({}, HINTS)[0])
        assert "3+ years of experience in Java" in job.description

    def test_unexpected_shape_raises(self):
        provider = PhonePeFeedProvider(client=client_with(lambda r: httpx.Response(200, json={"oops": 1})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)


def _rippling_item(job_id, name, *locations):
    return {
        "id": job_id,
        "name": name,
        "url": f"https://ats.rippling.com/rippling/jobs/{job_id}",
        "department": {"name": "Engineering"},
        "locations": list(locations),
        "language": "en-US",
    }


BLR = {"name": "Bengaluru, Karnataka", "country": "India", "countryCode": "IN", "workplaceType": "ON_SITE"}
NYC = {"name": "New York, NY", "country": "United States", "countryCode": "US", "workplaceType": "ON_SITE"}


class TestRipplingAts:
    def test_pages_and_merges_locations(self):
        pages = {
            "0": {"items": [_rippling_item("a1", "Software Engineer II", BLR), _rippling_item("a1", "Software Engineer II", NYC)],
                  "page": 0, "totalPages": 2},
            "1": {"items": [_rippling_item("b2", "Account Executive", NYC)], "page": 1, "totalPages": 2},
        }
        seen_pages = []

        def handler(request):
            assert "/api/v2/board/rippling/jobs" in request.url.path
            page = request.url.params["page"]
            seen_pages.append(page)
            return httpx.Response(200, json=pages[page])

        jobs = RipplingAtsProvider(client=client_with(handler)).fetch({"board": "rippling", "company_name": "Rippling"}, HINTS)

        assert seen_pages == ["0", "1"]
        assert len(jobs) == 2
        swe = next(j for j in jobs if j.external_id == "a1")
        assert swe.locations == ("Bengaluru, Karnataka, India", "New York, NY, United States")
        assert swe.precision == Precision.FIRST_SEEN
        assert swe.posted_at is None
        assert swe.remote is False
        assert swe.department == "Engineering"
        assert swe.url == "https://ats.rippling.com/rippling/jobs/a1"

    def test_requires_board(self):
        with pytest.raises(ProviderError):
            RipplingAtsProvider(client=client_with(lambda r: httpx.Response(200))).fetch({}, HINTS)


JOBVITE_PAGE = """
<p class="jv-cws-sr-only">Open Positions</p> <h3 class="h2">Accounting &amp; Finance</h3>
<table class="jv-job-list"> <thead> <tr> <th scope="col" class="jv-cws-sr-only">Job listing</th>
<th scope="col" class="jv-cws-sr-only">Job location</th> </tr> </thead> <tbody>
<tr> <td class="jv-job-list-name"> <a href="/nutanix/job/oUQKAfwO">Finance Manager</a> </td>
<td class="jv-job-list-location"> United States, United States </td> </tr> </tbody> </table>
<h3 class="h2">Engineering</h3> <table class="jv-job-list"> <tbody>
<tr> <td class="jv-job-list-name"> <a href="/nutanix/job/oXyZ123">Member of Technical Staff - 3</a> </td>
<td class="jv-job-list-location"> Bangalore, India </td> </tr> </tbody> </table>
"""


class TestJobvite:
    def test_parses_sections_and_rows(self):
        def handler(request):
            assert str(request.url) == "https://jobs.jobvite.com/nutanix/jobs"
            return httpx.Response(200, text=JOBVITE_PAGE)

        jobs = JobviteProvider(client=client_with(handler)).fetch({"company": "nutanix", "company_name": "Nutanix"}, HINTS)

        assert [(j.title, j.department, j.locations) for j in jobs] == [
            ("Finance Manager", "Accounting & Finance", ("United States, United States",)),
            ("Member of Technical Staff - 3", "Engineering", ("Bangalore, India",)),
        ]
        assert jobs[1].url == "https://jobs.jobvite.com/nutanix/job/oXyZ123"
        assert jobs[1].precision == Precision.FIRST_SEEN

    def test_details(self):
        page = '<div class="jv-job-detail-description"><p>4+ years building distributed systems</p></div></div>'
        provider = JobviteProvider(client=client_with(lambda r: httpx.Response(200, text=page)))
        job = Job(company="Nutanix", title="MTS", url="u", source="jobvite", detail_url="https://jobs.jobvite.com/nutanix/job/x")
        assert provider.fetch_details(job).description.strip() == "4+ years building distributed systems"

    def test_layout_change_raises(self):
        provider = JobviteProvider(client=client_with(lambda r: httpx.Response(200, text="<html>Access denied</html>")))
        with pytest.raises(ProviderError):
            provider.fetch({"company": "nutanix"}, HINTS)


def _talentbrew_row(job_id, title, location, category="Software Engineering"):
    return (
        f'<li data-remote="{job_id}" data-count="0" data-intuit-jobid="{job_id}" data-category-id="68357" '
        f"data-category='{category}' data-orig-location=\"\"> "
        f'<a href="/job/bengaluru/{title.lower().replace(" ", "-")}/27595/{job_id}" data-job-id="{job_id}" '
        f'class="sr-item" data-title="{title}"> <h2>{title}</h2> <span class="job-location">{location}</span> </a> '
        '<button class="js-save-job-btn"><span class="wai">Save </span></button> </li>'
    )


def _talentbrew_page(rows, total_pages):
    return (
        f'<section id="search-results" data-total-pages="{total_pages}" data-current-page="1"> '
        '<section id="search-results-list" class="search-results-list-wrapper"> <ul class="search-list"> '
        + " ".join(rows)
        + "</ul></section></section>"
    )


class TestTalentBrew:
    def test_pages_through_search_results(self):
        pages = {
            None: _talentbrew_page([_talentbrew_row("100375198304", "Staff Software Engineer", "Bengaluru, Karnataka, India")], 2),
            "2": _talentbrew_page([_talentbrew_row("100995747184", "Software Engineer 2", "San Diego, California")], 2),
        }

        def handler(request):
            assert request.url.path == "/search-jobs/software engineer"
            return httpx.Response(200, text=pages[request.url.params.get("p")])

        jobs = TalentBrewProvider(client=client_with(handler)).fetch(
            {"host": "jobs.intuit.com", "company_name": "Intuit"}, FetchHints(role_query="software engineer")
        )

        assert [j.title for j in jobs] == ["Staff Software Engineer", "Software Engineer 2"]
        job = jobs[0]
        assert job.url == "https://jobs.intuit.com/job/bengaluru/staff-software-engineer/27595/100375198304"
        assert job.external_id == "100375198304"
        assert job.locations == ("Bengaluru, Karnataka, India",)
        assert job.department == "Software Engineering"
        assert job.precision == Precision.FIRST_SEEN

    def test_stops_when_a_page_repeats(self):
        page = _talentbrew_page([_talentbrew_row("1", "Software Engineer", "Bengaluru")], 5)
        calls = []

        def handler(request):
            calls.append(request.url.params.get("p"))
            return httpx.Response(200, text=page)

        jobs = TalentBrewProvider(client=client_with(handler)).fetch({"host": "jobs.intuit.com"}, HINTS)
        assert len(jobs) == 1
        assert calls == [None, "2"]

    def test_layout_change_raises(self):
        provider = TalentBrewProvider(client=client_with(lambda r: httpx.Response(200, text="<html></html>")))
        with pytest.raises(ProviderError):
            provider.fetch({"host": "jobs.intuit.com"}, HINTS)


def _apple_row(job_id, title, team, date, location):
    return (
        '<li data-core-accordion-item="" role="listitem" class="rc-accordion-item"><div class="rc-accordion-button">'
        f'<div id="search-search-job-title-PIPE-{job_id}-1" class="d-flex flex-row row large-12 job-title job-list-item">'
        '<div class="d-flex flex-column column large-6 small-12 text-align-start job-title-link pr-40 w-51">'
        f'<h3><a class="link-inline t-intro word-wrap-break-word more" aria-label="{title} {job_id}" '
        f'href="/en-in/details/{job_id}/slug?team=SFTWR" data-discover="true">{title}</a></h3>'
        f'<span id="search-{team}-1" class="team-name mt-0">{team}</span>'
        f'<span class="job-posted-date" id="search-job-posted-date-1">{date}</span></div>'
        '<div class="column large-4 small-12 text-align-start job-title-location"><span class="a11y">Location</span>'
        f'<span class="table--advanced-search__location-sub" id="search-store-name-1">{location}</span></div></div>'
        f'<a class="link more" href="/en-in/details/{job_id}/slug?team=SFTWR">See full role description</a></div></li>'
    )


class TestApple:
    def test_parses_rows(self):
        html = _apple_row("200314122", "Software Engineer, Maps", "Software and Services", "23 Sept 2026", "Hyderabad")
        pages = {"1": html, "2": html}

        def handler(request):
            assert request.url.path == "/en-in/search"
            assert request.url.params["sort"] == "newest"
            return httpx.Response(200, text=pages.get(request.url.params["page"], ""))

        jobs = AppleJobsProvider(client=client_with(handler)).fetch({"locale": "en-in"}, FetchHints(role_query="software engineer"))

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Software Engineer, Maps"
        assert job.url == "https://jobs.apple.com/en-in/details/200314122/slug"
        assert job.external_id == "200314122"
        assert job.department == "Software and Services"
        assert job.locations == ("Hyderabad",)
        assert job.posted_at == datetime(2026, 9, 23, tzinfo=UTC)
        assert job.precision == Precision.DATE_ONLY

    def test_ids_with_suffix_and_no_slug(self):
        html = _apple_row("114438210-3337", "Software Engineer", "Software and Services", "22 Sept 2026", "Bengaluru")
        html += _apple_row("200600001", "iOS Engineer", "Software and Services", "22 Sept 2026", "Hyderabad").replace(
            "/details/200600001/slug", "/details/200600001")
        provider = AppleJobsProvider(client=client_with(lambda r: httpx.Response(200, text=html)))
        jobs = provider.fetch({}, HINTS)
        assert [j.external_id for j in jobs] == ["114438210-3337", "200600001"]
        assert jobs[1].url == "https://jobs.apple.com/en-in/details/200600001"

    def test_location_hint_maps_to_apple_code(self):
        seen = []

        def handler(request):
            seen.append(request.url.params.get("location"))
            return httpx.Response(200, text="")

        provider = AppleJobsProvider(client=client_with(handler))
        provider.fetch({"location_codes": {"India": "india-INDC"}}, FetchHints(location="india"))
        provider.fetch({"location_codes": {"India": "india-INDC"}}, FetchHints(location="Germany"))
        assert seen == ["india-INDC", None]

    def test_corporate_row_location(self):
        html = _apple_row("200684126-0321", "Software Eng - Content Management Systems", "Software and Services",
                          "22 Sept 2026", "x").replace(
            '<span class="table--advanced-search__location-sub" id="search-store-name-1">x</span>',
            '<span id="search-store-name-container-1">Bengaluru</span>')
        provider = AppleJobsProvider(client=client_with(lambda r: httpx.Response(200, text=html)))
        assert provider.fetch({}, HINTS)[0].locations == ("Bengaluru",)

    def test_unparsed_location_falls_back_to_the_searched_one(self):
        html = _apple_row("200684126-0321", "Software Eng - Content Management Systems", "Software and Services",
                          "22 Sept 2026", "x").replace('class="table--advanced-search__location-sub"', 'class="other"')
        provider = AppleJobsProvider(client=client_with(lambda r: httpx.Response(200, text=html)))
        narrowed = provider.fetch({"location_codes": {"india": "india-INDC"}}, FetchHints(location="India"))
        assert narrowed[0].locations == ("India",)
        assert provider.fetch({}, FetchHints(location="India"))[0].locations == ()

    def test_stops_once_a_page_predates_the_window(self):
        since = datetime(2026, 9, 22, tzinfo=UTC)
        old = _apple_row("1", "Software Engineer", "Hardware", "1 Sept 2026", "Bengaluru")
        calls = []

        def handler(request):
            calls.append(request.url.params["page"])
            return httpx.Response(200, text=old.replace('details/1/', f'details/{len(calls)}/'))

        AppleJobsProvider(client=client_with(handler)).fetch({}, FetchHints(since=since))
        assert calls == ["1"]


class TestShareChat:
    PAYLOAD = {
        "data": {
            "careersList": [
                {
                    "title": "Engineering",
                    "data": [
                        {
                            "requisitionId": 2450,
                            "requisitionTitle": "Software Development Engineer II - Backend",
                            "orgUnitName": "Engineering",
                            "officeLocationNames": ["Bangalore"],
                            "yrsOfExpMax": 5,
                            "yrsOfExpMin": 2,
                            "employmentType": "full-time",
                            "jobDescription": "<p>Build feeds.</p>",
                            "createdDate": 1788931377196,
                            "approvedDate": 1788934944655,
                        }
                    ],
                }
            ]
        }
    }

    def test_parses_postings_with_stated_experience(self):
        provider = ShareChatProvider(client=client_with(lambda r: httpx.Response(200, json=self.PAYLOAD)))
        jobs = provider.fetch({"company_name": "ShareChat"}, HINTS)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Software Development Engineer II - Backend"
        assert job.locations == ("Bangalore",)
        assert job.posted_at == datetime.fromtimestamp(1788934944.655, tz=UTC)
        assert job.description.startswith("Experience required: 2-5 years")
        assert "Build feeds." in job.description

    def test_unexpected_shape_raises(self):
        provider = ShareChatProvider(client=client_with(lambda r: httpx.Response(200, json={"data": {}})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)


def _undated(title, source="jobvite"):
    return Job(company="Nutanix", title=title, url=f"https://x/{title}", source=source, precision=Precision.FIRST_SEEN)


class TestFirstSeenStore:
    def test_first_run_is_backlog_then_new_jobs_are_dated(self, tmp_path):
        path = tmp_path / "first_seen.json"
        day1 = datetime(2026, 9, 23, 3, 30, tzinfo=UTC)
        day2 = day1 + timedelta(days=1)

        store = FirstSeenStore(path)
        first = store.date([_undated("Old Job")], now=day1)
        assert first[0].posted_at is None  # already listed when tracking began
        store.save(now=day1)

        store = FirstSeenStore(path)
        second = {j.title: j.posted_at for j in store.date([_undated("Old Job"), _undated("New Job")], now=day2)}
        assert second == {"Old Job": None, "New Job": day2}
        store.save(now=day2)

        # The date sticks on later runs.
        store = FirstSeenStore(path)
        third = store.date([_undated("New Job")], now=day2 + timedelta(days=1))
        assert third[0].posted_at == day2

    def test_dated_jobs_pass_through(self, tmp_path):
        dated = Job(company="A", title="T", url="u", source="greenhouse", posted_at=datetime(2026, 9, 1, tzinfo=UTC),
                    precision=Precision.EXACT)
        assert FirstSeenStore(tmp_path / "s.json").date([dated]) == [dated]

    def test_forgets_long_gone_postings(self, tmp_path):
        path = tmp_path / "first_seen.json"
        start = datetime(2026, 7, 1, tzinfo=UTC)
        store = FirstSeenStore(path)
        store.date([_undated("Gone")], now=start)
        store.save(now=start + timedelta(days=90))
        assert FirstSeenStore(path).boards["Nutanix|jobvite"]["jobs"] == {}

    def test_corrupt_file_starts_fresh(self, tmp_path):
        path = tmp_path / "first_seen.json"
        path.write_text("not json")
        assert FirstSeenStore(path).boards == {}


def test_software_eng_abbreviation_matches_role():
    from faangscout.filters.role import RoleKeywordFilter
    from faangscout.models import SearchCriteria

    jobs = [Job(company="Apple", title=t, url=t, source="apple_jobs")
            for t in ("Software Eng - Content Management Systems", "Engineering Program Manager")]
    kept, _ = RoleKeywordFilter().apply(jobs, SearchCriteria.build(["Apple"], role="software engineer"))
    assert [j.title for j in kept] == ["Software Eng - Content Management Systems"]


class TestAtlassian:
    LISTING = [{
        "portalJobPost": {"portalId": 17, "id": 25590,
                          "portalUrl": "https://globalcareers-atlassian.icims.com/jobs/25590/software-engineer/job",
                          "updatedDate": "2026-09-22 12:42 AM"},
        "id": 25590, "portalId": 17, "title": "Software Engineer, Backend ",
        "locations": ["Bengaluru - India", "Remote - India"], "category": "Engineering",
        "overview": "<p>Working at Atlassian</p>", "responsibilities": "<p>Build Jira.</p>",
        "qualifications": "<ul><li>3+ years of experience</li></ul>",
        "applyUrl": "https://globalcareers-atlassian.icims.com/jobs/25590/login",
    }]

    def test_parses_listing(self):
        from faangscout.providers.atlassian import AtlassianProvider

        provider = AtlassianProvider(client=client_with(lambda r: httpx.Response(200, json=self.LISTING)))
        job = provider.fetch({"company_name": "Atlassian"}, HINTS)[0]
        assert job.title == "Software Engineer, Backend"
        assert job.url.startswith("https://globalcareers-atlassian.icims.com/jobs/25590/")
        assert job.locations == ("Bengaluru - India", "Remote - India")
        assert job.department == "Engineering"
        assert job.posted_at == datetime(2026, 9, 22, 0, 42, tzinfo=UTC)
        assert job.precision == Precision.APPROXIMATE
        assert "3+ years of experience" in job.description and "Build Jira." in job.description

    def test_unexpected_shape_raises(self):
        from faangscout.providers.atlassian import AtlassianProvider

        provider = AtlassianProvider(client=client_with(lambda r: httpx.Response(200, json={"error": "x"})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)


def test_talentbrew_palo_alto_template():
    row = ('<li class="section29__search-results-li"> <a class="section29__search-results-link" '
           'href="/en/job/hyderabad/staff-software-engineer/47263/97425296192" data-job-id="97425296192"> '
           '<h2 class="section29__search-results-job-title">Staff Software Engineer</h2> '
           '<div class="section29__result-info-container"> <span class="section29__result-location newLoc">500081, India</span> '
           '</div> </a> </li>')
    html = (f'<section id="search-results" data-total-pages="1"> <section id="search-results-list"> '
            f'<ul class="section29__search-results-ul"> {row} </ul></section></section>')

    def handler(request):
        assert request.url.path == "/en/search-jobs/software engineer"
        return httpx.Response(200, text=html)

    jobs = TalentBrewProvider(client=client_with(handler)).fetch(
        {"host": "jobs.paloaltonetworks.com", "base_url": "https://jobs.paloaltonetworks.com/en"},
        FetchHints(role_query="software engineer"),
    )
    assert [(j.title, j.locations, j.url) for j in jobs] == [(
        "Staff Software Engineer", ("500081, India",),
        "https://jobs.paloaltonetworks.com/en/job/hyderabad/staff-software-engineer/47263/97425296192",
    )]


class TestGoldmanSachs:
    ITEM = {"roleId": "154399_GS_MID_CAREER", "corporateTitle": "Associate",
            "jobTitle": "The Core Engineering-Bengaluru-Associate-Software Engineering",
            "jobFunction": "Software Engineering",
            "locations": [{"primary": True, "state": "Karnataka", "country": "India", "city": "Bengaluru"}],
            "status": "POSTED", "division": "The Core Engineering", "externalSource": {"sourceId": "154399"}}

    def test_graphql_search_filtered_to_the_location(self):
        import json as _json
        from faangscout.providers.goldman_sachs import GoldmanSachsProvider

        sent = []

        def handler(request):
            sent.append(_json.loads(request.content))
            return httpx.Response(200, json={"data": {"roleSearch": {"totalCount": 1, "items": [self.ITEM]}}})

        jobs = GoldmanSachsProvider(client=client_with(handler)).fetch({}, FetchHints(location="India"))
        search = sent[0]["variables"]["searchQueryInput"]
        assert search["filters"] == [{"filterCategoryType": "LOCATION", "filters": [{"filter": "India", "subFilters": []}]}]
        assert search["sort"] == {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"}
        job = jobs[0]
        assert job.url == "https://higher.gs.com/roles/154399"
        assert job.locations == ("Bengaluru, Karnataka, India",)
        assert job.precision == Precision.FIRST_SEEN

    def test_title_matches_role_and_ladder(self):
        from faangscout.companies.levels import company_level
        from faangscout.filters.role import RoleKeywordFilter
        from faangscout.models import SearchCriteria

        job = Job(company="Goldman Sachs", title=self.ITEM["jobTitle"], url="u", source="goldman_sachs")
        kept, _ = RoleKeywordFilter().apply([job], SearchCriteria.build(["x"], role="software engineer"))
        assert kept == [job]
        assert company_level("Goldman Sachs", job.title).sde == 2

    def test_graphql_errors_raise(self):
        from faangscout.providers.goldman_sachs import GoldmanSachsProvider

        provider = GoldmanSachsProvider(client=client_with(lambda r: httpx.Response(200, json={"errors": [{"message": "bad"}]})))
        with pytest.raises(ProviderError):
            provider.fetch({}, HINTS)

    def test_details_from_next_data(self):
        from faangscout.providers.goldman_sachs import GoldmanSachsProvider

        page = ('<html><script id="__NEXT_DATA__" type="application/json">'
                '{"props": {"pageProps": {"role": {"descriptionHtml": "<p>3+ years of experience in Java</p>"}}}}'
                '</script></html>')
        provider = GoldmanSachsProvider(client=client_with(lambda r: httpx.Response(200, text=page)))
        job = Job(company="Goldman Sachs", title="t", url="u", source="goldman_sachs", detail_url="https://higher.gs.com/roles/1")
        assert provider.fetch_details(job).description.strip() == "3+ years of experience in Java"
