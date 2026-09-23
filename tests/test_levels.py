"""Company ladders: company-specific level names -> SDE level and years."""

import pytest

from faangscout.companies.levels import company_level, parse_ladders
from faangscout.experience import assess
from faangscout.filters.experience import ExperienceFilter
from faangscout.models import Job, SearchCriteria


def _job(company, title, description=""):
    return Job(company=company, title=title, url=f"https://x/{title}", source="t", description=description)


@pytest.mark.parametrize(
    "company, title, sde",
    [
        ("Salesforce", "Member of Technical Staff - Java", 2),
        ("Salesforce", "Software Engineering MTS", 2),
        ("Salesforce", "Senior Member of Technical Staff", 3),
        ("Salesforce", "Software Engineering SMTS", 3),
        ("Salesforce", "LMTS - Backend", 4),
        ("Salesforce", "AMTS", 1),
        ("Walmart", "Software Engineer III", 2),
        ("Walmart", "Software Engineer II", 1),
        ("Walmart", "Senior Software Engineer", 3),
        ("Adobe", "Member of Technical Staff 2", 2),
        ("Adobe", "MTS-2 (Java)", 2),
        ("Adobe", "Computer Scientist", 3),
        ("Adobe", "Senior Computer Scientist", 4),
        ("Nutanix", "Member of Technical Staff - 3 (Java/Golang)", 2),
        ("Nutanix", "Linux Kernel Developer- MTS(4)", 3),
        ("Cohesity", "Software Engineer 3 (MTS 3)", 2),
        ("Cohesity", "Software Engineer 4 (MTS 4)", 3),
        ("Sprinklr", "Senior Product Engineer", 2),
        ("Sprinklr", "Product Engineer", 1),
        ("Qualcomm", "Engineer, Senior -Audio", 2),
        ("Qualcomm", "Sr Lead Engineer", 3),
        ("Qualcomm", "Staff Engineer - IMS Software Development", 3),
        ("Qualcomm", "Engineer- Display", 1),
        ("NVIDIA", "Senior System Software Engineer", 2),
        ("Mastercard", "Senior Software Engineer", 2),
        ("Mastercard", "Lead Software Engineer (Java)", 3),
        ("Mastercard", "Software Engineer II", 2),
        ("JPMorgan Chase", "Software Engineer III - Java AWS", 2),
        ("JPMorgan Chase", "Senior Lead Software Engineer Java/Golang", 4),
        ("Microsoft", "Software Engineer II - GitHub", 2),
        ("Microsoft", "Senior Software Engineer - Hyderabad", 3),
        ("Amazon", "SDE II, Amazon Business Operations", 2),
        ("Amazon", "Software Development Engineer, AI Trust & Innovation", 1),
        ("Amazon", "Software Dev Engineer II, Business Data Technologies", 2),
        ("D. E. Shaw", "Senior Member Technical", 2),
    ],
)
def test_ladders(company, title, sde):
    level = company_level(company, title)
    assert level is not None and level.sde == sde, (title, level)


def test_unknown_company_or_title_has_no_ladder_level():
    assert company_level("Stripe", "Software Engineer") is None
    assert company_level("Salesforce", "Account Executive") is None


def test_company_name_is_matched_loosely():
    assert company_level("jpmorgan chase", "Software Engineer III").sde == 2


def test_parse_ladders_open_ended_years():
    ladders = parse_ladders({"companies": {"Acme": [{"match": "x", "name": "X", "sde": 3, "years": [5, None]}]}})
    level = ladders["acme"][0]
    assert (level.min_years, level.max_years, level.label) == (5.0, None, "Acme X")


class TestAssess:
    def test_stated_years_win_over_the_ladder(self):
        req = assess(_job("Cohesity", "Software Engineer 4 (MTS 4)", "8+ years of experience designing systems"))
        assert (req.basis, req.min_years, req.sde) == ("description", 8, 3)
        assert req.display() == "8+ yrs · ≈ SDE-3"

    def test_ladder_replaces_the_generic_title_guess(self):
        req = assess(_job("Mastercard", "Senior Software Engineer"))
        assert (req.basis, req.min_years, req.max_years, req.sde) == ("ladder", 3, 6, 2)
        assert req.display() == "~3–6 yrs (Mastercard Senior SE) · ≈ SDE-2"

    def test_generic_title_gets_an_sde_level_when_plain(self):
        req = assess(_job("Kotak Mahindra Bank", "Software Developer Engineer 2-Digital Banking"))
        assert (req.basis, req.sde) == ("title", 2)
        assert assess(_job("Stripe", "Software Engineer")).sde is None


def _run(jobs, experience):
    kept, rejected = ExperienceFilter().apply(jobs, SearchCriteria.build(["x"], experience=experience))
    return [j.title for j in kept], {r.job.title: r.reason for r in rejected}


class TestEitherCounts:
    def test_mastercard_senior_with_no_stated_years_is_kept(self):
        kept, _ = _run([_job("Mastercard", "Senior Software Engineer")], 3)
        assert kept == ["Senior Software Engineer"]

    def test_walmart_se_iii_is_kept(self):
        kept, _ = _run([_job("Walmart", "Software Engineer III")], 3)
        assert kept == ["Software Engineer III"]

    def test_sde2_ladder_level_outside_the_range_still_counts(self):
        # Salesforce MTS reads ~1-4 years; with 5 years it's out of range but SDE-2.
        kept, _ = _run([_job("Salesforce", "Member of Technical Staff")], {"years": 5, "sde": 2})
        assert kept == ["Member of Technical Staff"]

    def test_stated_years_still_decide(self):
        kept, rejected = _run([_job("Cohesity", "Software Engineer 3 (MTS 3)", "Requires 6+ years of experience")], 3)
        assert kept == [] and "requires 6+ yrs" in rejected["Software Engineer 3 (MTS 3)"]

    def test_other_ladder_levels_are_dropped(self):
        kept, rejected = _run([_job("Salesforce", "Senior Member of Technical Staff"),
                               _job("Salesforce", "LMTS")], 3)
        assert kept == []
        assert rejected["Senior Member of Technical Staff"].startswith("requires ~4–8 yrs (Salesforce SMTS)")


@pytest.mark.parametrize(
    "title, sde",
    [("Software Engineer III, Infrastructure, Google Cloud Storage", 2),
     ("Software Engineer II, YouTube", 1),
     ("Senior Software Engineer, Search", 3),
     ("Senior Staff Software Engineer, YouTube Create", 5),
     ("Software Engineer, PhD, Early Career, 2026", 1)],
)
def test_google_ladder(title, sde):
    assert company_level("Google", title).sde == sde
