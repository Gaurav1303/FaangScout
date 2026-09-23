from datetime import UTC, datetime, timedelta

import httpx
import pytest

from faangscout.experience import assess, mentions, requirement_lines
from faangscout.filters import FilterPipeline
from faangscout.filters.base import Filter
from faangscout.filters.experience import ExperienceFilter
from faangscout.filters.location import LocationFilter, location_matches
from faangscout.models import ExperienceReq, Job, SearchCriteria
from faangscout.providers.amazon import AmazonProvider
from faangscout.providers.eightfold import EightfoldProvider
from faangscout.providers.greenhouse import GreenhouseProvider
from faangscout.providers.oracle_hcm import OracleHcmProvider
from faangscout.providers.workday import WorkdayProvider
from faangscout.report import render_markdown
from faangscout.models import ScoredJob, ScoutReport
from faangscout.scout import make_enricher

NOW = datetime.now(UTC)


def job(title="Software Engineer", *, locations=("Pune, India",), description="", source="test", detail_url=""):
    return Job(company="Acme", title=title, url="https://x", source=source, posted_at=NOW,
               locations=locations, description=description, detail_url=detail_url)


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("location", [
    "Pune, India", "Hyderabad, TS, IN", "Bengaluru, Karnataka, IND", "India - Hyderabad",
    "Bangalore - India (Office)", "Noida", "Bangalore", "Pune - Panchshil - India (Office)",
    "Remote - India", "Gurgaon, Haryana",
])
def test_india_locations_seen_live(location):
    assert location_matches(location, "India")


@pytest.mark.parametrize("location", [
    "Indianapolis, IN, US",  # IN mid-string is Indiana
    "Mountain View, CA, US", "Seattle, Washington, USA", "Remote - US", "Dublin, Ireland",
    "Singapore", "Hsinchu, Hsinchu City, TW", "London, UK", "Boca Raton",
])
def test_non_india_locations(location):
    assert not location_matches(location, "India")


def test_other_countries_match_by_name():
    assert location_matches("Dublin, Ireland", "Ireland")


def test_location_filter_keeps_any_indian_location_and_drops_unknown():
    criteria = SearchCriteria.build(["a"], posted_within_hours=None, location="India")
    multi = job(locations=("Seattle, WA, US", "Hyderabad, TS, IN"))
    us = job(locations=("Seattle, WA, US",))
    none = job(locations=())
    kept, rejected = LocationFilter().apply([multi, us, none], criteria)
    assert kept == [multi]
    assert {r.reason for r in rejected} == {"no location listed", "'Seattle, WA, US' is not in India"}


# --------------------------------------------------------------------------- #
# Experience extraction - text from live postings seen on 2026-09-23
# --------------------------------------------------------------------------- #

AMAZON = """Basic qualifications:
- 3+ years of non-internship professional software development experience
- 2+ years of non-internship design or architecture experience
- 1+ years of Object Oriented Design experience
Preferred qualifications:
- 5+ years of full software development life cycle experience"""

MICROSOFT = """Required Qualifications:
Bachelor's Degree in Computer Science AND 2+ years technical engineering experience with coding
OR Master's Degree in Computer Science AND 1+ year(s) technical engineering experience
For Senior: Bachelor's Degree AND 4+ years technical engineering experience
Preferred Qualifications:
Bachelor's Degree AND 6+ years technical engineering experience"""

JPMORGAN = """Required qualifications, capabilities, and skills
Formal training or certification on software engineering concepts and 3+ years applied experience
Preferred qualifications, capabilities, and skills
Familiarity with modern front-end technologies"""


@pytest.mark.parametrize("title,text,label,fits", [
    ("Software Development Engineer II", AMAZON, "3+ yrs", True),        # preferred 5+ ignored
    ("Software Engineer 2 / Senior Software Engineer", MICROSOFT, "2+ yrs", True),  # two levels -> lenient
    ("Software Engineer III", JPMORGAN, "3+ yrs", True),
    ("Lead Software Engineer", "Formal training and 5+ years applied experience", "5+ yrs", False),
    ("Software Engineer", "We are looking for 3-5 years of experience in Java.", "3–5 yrs", True),
    ("Software Engineer", "You have 2 to 4 years of experience building services.", "2–4 yrs", True),
    ("Software Engineer", "Minimum of three years of experience in backend development", "3+ yrs", True),
    ("Software Engineer", "5+ years of experience preferred\n2+ years of experience required", "2+ yrs", True),
    ("Software Engineer", "0-2 years of experience", "0–2 yrs", False),  # fresher role
    ("Sr. Data Engineer", "4-6 yrs of experience in data engineering", "4–6 yrs", False),
    ("Software Engineer", "Serving customers for over 50 years.\n2+ years of experience", "2+ yrs", True),
])
def test_requirement_from_description(title, text, label, fits):
    req = assess(job(title, description=text))
    assert req.basis == "description"
    assert req.label() == label
    assert req.admits(3) is fits


@pytest.mark.parametrize("title,label,fits", [
    ("Senior Software Engineer", "~5+ yrs (title)", False),
    ("Software Engineer I", "~0–2 yrs (title)", False),
    ("Software Engineer II", "~2–6 yrs (title)", True),
    ("SDE 2", "~2–6 yrs (title)", True),
    ("Software Engineer 4 (MTS 4)", "~5+ yrs (title)", False),
    ("Principal Software Engineer", "~10+ yrs (title)", False),
])
def test_requirement_from_title_when_description_is_silent(title, label, fits):
    req = assess(job(title, description="Great team, interesting problems."))
    assert req.basis == "title"
    assert req.label() == label
    assert req.admits(3) is fits


def test_unknown_when_nothing_stated():
    req = assess(job("Software Engineer (Full Stack)", description="Build things."))
    assert req == ExperienceReq()
    assert req.label() == "not stated" and req.admits(3) is None


def test_preferred_section_is_dropped_until_a_required_header():
    lines = requirement_lines("Preferred:\n7+ years\nMinimum qualifications:\n2+ years")
    assert lines == ["Minimum qualifications:", "2+ years"]


def test_implausible_numbers_ignored():
    assert mentions("Join a team with 25+ years of experience") == []


# --------------------------------------------------------------------------- #
# Experience filter + pipeline enrichment
# --------------------------------------------------------------------------- #

def test_experience_filter_annotates_and_keeps_unknown_by_default():
    criteria = SearchCriteria.build(["a"], posted_within_hours=None, experience=3)
    fits, senior, unknown = job(description="2+ years of experience"), job("Senior SWE"), job(description="Nice team")
    kept, rejected = ExperienceFilter().apply([fits, senior, unknown], criteria)
    assert [k.experience.label() for k in kept] == ["2+ yrs", "not stated"]
    assert "requires ~5+ yrs (title)" in rejected[0].reason


def test_experience_filter_can_drop_unknown():
    criteria = SearchCriteria.build(["a"], posted_within_hours=None, experience={"years": 3, "include_unknown": False})
    kept, _ = ExperienceFilter().apply([job(description="Nice team")], criteria)
    assert kept == []


def test_pipeline_enriches_only_jobs_that_reach_the_experience_filter():
    enriched: list[str] = []

    def enricher(jobs):
        enriched.extend(j.location_text for j in jobs)
        return [j if j.description else Job(**{**_fields(j), "description": "3+ years of experience"}) for j in jobs]

    criteria = SearchCriteria.build(["a"], posted_within_hours=None, location="India", experience=3)
    india, us = job(locations=("Pune, India",)), job(locations=("Seattle, WA, US",))
    kept, _, _ = FilterPipeline(enricher=enricher).run([india, us], criteria)
    assert enriched == ["Pune, India"]  # the US job was dropped before any detail fetch
    assert kept[0].experience.label() == "3+ yrs"


def test_pipeline_does_not_enrich_without_a_description_filter():
    calls = []
    FilterPipeline(enricher=lambda js: calls.append(js) or js).run([job()], SearchCriteria.build(["a"]))
    assert calls == []


def _fields(j):
    return {f: getattr(j, f) for f in j.__dataclass_fields__}


# --------------------------------------------------------------------------- #
# Provider detail fetches - response shapes copied from the live endpoints
# --------------------------------------------------------------------------- #

def test_workday_detail():
    url = "https://mastercard.wd1.myworkdayjobs.com/wday/cxs/mastercard/CorporateCareers/job/Pune-India/Software-Engineer-II_R-289618"
    payload = {"jobPostingInfo": {"title": "Software Engineer II", "startDate": "2026-09-22",
               "jobDescription": "<p><b>Overview</b></p>What You Will Do<br>•Design<br>Qualifications<br>3+ years of Java experience"}}

    def handler(request):
        assert str(request.url) == url
        return httpx.Response(200, json=payload)

    j = WorkdayProvider._to_job({"title": "Software Engineer II", "externalPath": "/job/Pune-India/Software-Engineer-II_R-289618",
                                 "postedOn": "Posted Today"}, company="Mastercard",
                                host="mastercard.wd1.myworkdayjobs.com", tenant="mastercard", site="CorporateCareers")
    assert j.detail_url == url
    out = WorkdayProvider(client=client_with(handler)).fetch_details(j)
    assert "3+ years of Java experience" in out.description.splitlines()


def test_oracle_detail():
    payload = {"items": [{"Id": "210785193", "Title": "Software Engineer III",
               "ExternalDescriptionStr": "<div>Intro<br><strong>Required qualifications</strong></div><ul><li>3+ years applied experience</li></ul>"}]}
    j = OracleHcmProvider._to_job({"Id": "210785193", "Title": "Software Engineer III", "PostedDate": "2026-09-22"},
                                  company="JPMorgan Chase", job_base="https://jpmc/job",
                                  detail_url="https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{id}%22,siteNumber=CX_1001")
    assert "Id=%22210785193%22,siteNumber=CX_1001" in j.detail_url
    out = OracleHcmProvider(client=client_with(lambda r: httpx.Response(200, json=payload))).fetch_details(j)
    assert assess(out).label() == "3+ yrs"


def test_eightfold_detail():
    payload = {"status": 200, "data": {"id": 1970393556981791, "jobDescription": "<b>Qualifications</b><br>2+ years technical engineering experience"}}
    j = EightfoldProvider._to_job({"id": 1970393556981791, "name": "SWE 2", "positionUrl": "/careers/job/1970393556981791"},
                                  company="Microsoft", base="https://apply.careers.microsoft.com", domain="microsoft.com")
    assert j.detail_url == ("https://apply.careers.microsoft.com/api/pcsx/position_details"
                            "?position_id=1970393556981791&domain=microsoft.com&hl=en")
    out = EightfoldProvider(client=client_with(lambda r: httpx.Response(200, json=payload))).fetch_details(j)
    assert assess(out).label() == "2+ yrs"
    assert EightfoldProvider.detail_concurrency == 2


def test_amazon_sections_keep_preferred_out_of_the_requirement():
    entry = {"title": "SDE II", "job_path": "/en/jobs/1/sde-ii", "posted_date": "September 22, 2026",
             "basic_qualifications": "- 3+ years of non-internship professional software development experience",
             "preferred_qualifications": "- 5+ years of full software development life cycle experience",
             "description": "Build things."}
    j = AmazonProvider._to_job(entry, company="Amazon", base="https://www.amazon.jobs")
    assert assess(j).label() == "3+ yrs"


def test_greenhouse_escaped_html_is_decoded():
    payload = {"jobs": [{"id": 1, "title": "SWE", "absolute_url": "u", "updated_at": "2026-09-22T00:00:00Z",
                         "location": {"name": "Pune, India"}, "departments": [],
                         "content": "&lt;p&gt;About us&lt;/p&gt;&lt;ul&gt;&lt;li&gt;3+ years of experience&lt;/li&gt;&lt;/ul&gt;"}]}
    [j] = GreenhouseProvider(client=client_with(lambda r: httpx.Response(200, json=payload))).fetch({"board": "a"}, _hints())
    assert "<" not in j.description
    assert j.description.splitlines() == ["About us", "3+ years of experience"]


def _hints():
    from faangscout.models import FetchHints
    return FetchHints()


def test_enricher_keeps_job_and_records_failure():
    failures: list[str] = []
    enrich = make_enricher(client_with(lambda r: httpx.Response(404)), failures)
    j = job(source="workday", detail_url="https://x.wd1.myworkdayjobs.com/wday/cxs/x/S/job/1")
    assert enrich([j]) == [j]
    assert len(failures) == 1 and "HTTP 404" in failures[0]


def test_markdown_shows_experience_column_and_scope():
    j = Job(company="A", title="SWE", url="https://u", source="s", posted_at=NOW, locations=("Pune, India",),
            experience=ExperienceReq(3, None, "description"))
    md = render_markdown(ScoutReport(jobs=[ScoredJob(j)]), role="software engineer", hours=24, location="India", experience=3)
    assert "in **India**" in md and "fits **3 yrs** experience" in md
    assert "| Experience |" in md and "| 3+ yrs |" in md


def test_markdown_lists_experience_exclusions_and_warnings():
    from faangscout.models import Rejection
    kept = Job(company="A", title="SWE II", url="https://u", source="s", posted_at=NOW)
    senior = Job(company="A", title="Senior SWE", url="https://s", source="s", posted_at=NOW)
    report = ScoutReport(
        jobs=[ScoredJob(kept)],
        rejections=[Rejection(senior, "experience", "requires 5+ yrs ('5+ years of experience')"),
                    Rejection(senior, "location", "not in India")],
        warnings=["couldn't fetch the full posting for 1 job(s)", "could not resolve: Apple"],
    )
    md = render_markdown(report, show_excluded=True)
    assert "Excluded by experience (1)" in md and "requires 5+ yrs" in md
    assert "not in India" not in md  # only experience exclusions are listed
    assert "⚠️ couldn't fetch the full posting" in md
    assert "could not resolve" not in md  # already shown as "Not covered yet"
    assert "Excluded by experience" not in render_markdown(report)  # off by default (e.g. email comment)


# Lines exactly as html_to_text rendered them from live postings (run 11).
MS_SWE2_SENIOR_LIVE = "\n".join([
    "Qualifications",
    "Required/Minimum Qualifications",
    "\u200b \u200bBachelor's Degree in Computer Science or related technical field AND 7+ years technical "
    "engineering experience with coding in languages including, but not limited to, C, C++, C#, Java, "
    "JavaScript, or Python OR equivalent experience.",
    "Job Requirements: Other & Additional",
    "This position will be required to pass the Microsoft Cloud background check upon hire/transfer and every two years thereafter",
    "Preferred/Additional Qualifications",
    "\u200b \u200b- 5+ years of software engineering experience building large scale production services.",
])
MS_CORE_AI_LIVE = "\n".join([
    "Qualifications",
    "(Required and Preferred)",
    "Bachelor's / Master\u2019s Degree in Computer Science or related technical field AND 4+ years technical "
    "engineering experience with coding in languages including, but not limited to, C#, Java, or Python OR equivalent experience",
])
JPMC_SWE3_LIVE = "\n".join([
    "Required qualifications, capabilities, and skills",
    "Formal training or certification on software engineering concepts and 7+ years applied experience as a full stack developer",
    "Preferred qualifications, capabilities, and skills",
    "Exposure to cloud technologies",
])


@pytest.mark.parametrize("title,text,label", [
    ("Software Engineer 2 / Senior Software Engineer", MS_SWE2_SENIOR_LIVE, "7+ yrs"),
    ("Software Engineer II - AI/ML Infrastructure CoreAI", MS_CORE_AI_LIVE, "4+ yrs"),
    ("Software Engineer III", JPMC_SWE3_LIVE, "7+ yrs"),
])
def test_live_descriptions(title, text, label):
    assert assess(job(title, description=text)).label() == label


def test_or_masters_alternative_on_the_same_line_keeps_the_bachelors_requirement():
    line = ("Bachelor's Degree AND 2+ years technical engineering experience "
            "OR Master\u2019s Degree AND 1+ year(s) technical engineering experience OR equivalent experience.")
    assert assess(job(description=line)).label() == "2+ yrs"


def test_line_that_is_only_an_alternative_route_is_ignored():
    text = "Bachelor's Degree AND 3+ years experience\nOR Master\u2019s Degree AND 1+ year experience"
    assert assess(job(description=text)).label() == "3+ yrs"


def test_preferably_is_optional():
    text = "2+ years of experience\nengineers with preferably 8 years of experience in analog design"
    assert assess(job(description=text)).label() == "2+ yrs"
