"""A second search profile (recruiter) next to the default one, and email delivery."""

from pathlib import Path
from unittest import mock

import pytest

from faangscout.cli import _experience_filter, apply_config, build_parser, load_config
from faangscout.experience import assess
from faangscout.filters.experience import ExperienceFilter, parse_config
from faangscout.filters.role import RoleKeywordFilter as RoleFilter
from faangscout.models import Job, SearchCriteria
from faangscout.normalize import expand_role_query
from faangscout.notify.email import EmailConfigError, build_message, markdown_to_html, send_markdown_email
from faangscout.report import company_summary, role_noun

ROOT = Path(__file__).resolve().parents[1]


def job(title, company="Acme", description=None):
    return Job(company=company, title=title, url=f"https://x/{title}", source="greenhouse",
               external_id=title, description=description)


def args_for(config: Path, *argv):
    args = build_parser().parse_args(["--config", str(config), *argv])
    apply_config(args)
    return args


# --------------------------------------------------------------------------- #
# Config: extends
# --------------------------------------------------------------------------- #

def test_profile_extends_base_and_overrides(tmp_path):
    (tmp_path / "base.yaml").write_text("role: software engineer\nexperience: 3\ncompanies: [Stripe, Adobe]\n")
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "rec.yaml").write_text("extends: ../base.yaml\nrole: recruiter\nexperience: {years: 5, sde: null}\n")
    config = load_config(tmp_path / "p" / "rec.yaml")
    assert config["companies"] == ["Stripe", "Adobe"]
    assert config["role"] == "recruiter"
    assert "extends" not in config


def test_extending_itself_is_an_error(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: a.yaml\n")
    with pytest.raises(ValueError):
        load_config(tmp_path / "a.yaml")


def test_default_config_criteria_unchanged():
    """scout.yaml (the existing daily email) still builds exactly the same search."""
    args = args_for(ROOT / "scout.yaml")
    assert args.role == "software engineer"
    assert args.location == "India"
    assert args.hours == 24.0
    assert _experience_filter(args) == 3.0  # a plain number: default sde/ladders
    assert parse_config(_experience_filter(args)) == (3.0, True, 2, True)


def test_recruiter_profile_uses_the_same_companies():
    base = args_for(ROOT / "scout.yaml")
    rec = args_for(ROOT / "profiles" / "recruiter.yaml")
    assert rec.companies == base.companies and len(rec.companies) > 50
    assert rec.role == "recruiter"
    assert rec.location == "India"
    assert rec.hours == 24.0
    assert _experience_filter(rec) == {"years": 5.0, "sde": None, "ladders": False}
    assert parse_config(_experience_filter(rec)) == (5.0, True, None, False)


# --------------------------------------------------------------------------- #
# Matching recruiter titles
# --------------------------------------------------------------------------- #

def test_software_engineer_expansion_unaffected_by_recruiter_family():
    q = expand_role_query("software engineer")
    terms = set(q.required) | set(q.optional)
    assert not terms & {"recruiter", "recruiting", "talent acquisition", "sourcer"}


@pytest.mark.parametrize("title, keep", [
    ("Senior Technical Recruiter", True),
    ("Talent Acquisition Partner", True),
    ("Recruitment Lead - Engineering", True),
    ("Sourcer, Tech Hiring", True),
    ("Recruiting Coordinator", True),
    ("Senior Software Engineer", False),
    ("Account Executive", False),
])
def test_recruiter_role_filter(title, keep):
    criteria = SearchCriteria.build(["a"], role="recruiter", posted_within_hours=None)
    kept, _ = RoleFilter().apply([job(title)], criteria)
    assert bool(kept) is keep


def test_software_search_still_skips_recruiters():
    criteria = SearchCriteria.build(["a"], role="software engineer", posted_within_hours=None)
    kept, _ = RoleFilter().apply([job("Technical Recruiter"), job("Software Engineer II")], criteria)
    assert [k.title for k in kept] == ["Software Engineer II"]


# --------------------------------------------------------------------------- #
# Experience without engineering ladders
# --------------------------------------------------------------------------- #

def test_ladders_off_reads_titles_generically():
    lead = job("Lead Recruiter", company="Salesforce")
    assert assess(lead).basis == "ladder"  # Salesforce "Lead" = LMTS, 8+ yrs
    generic = assess(lead, use_ladders=False)
    assert generic.basis == "title" and generic.admits(5)


def test_recruiter_experience_filter():
    criteria = SearchCriteria.build(["a"], posted_within_hours=None,
                                    experience={"years": 5, "sde": None, "ladders": False})
    jobs = [
        job("Lead Recruiter", company="Salesforce"),               # generic Lead: 5+ -> fits
        job("Recruiter", description="4-6 years of recruiting experience"),
        job("Recruiter", description="8+ years of experience in hiring"),
        job("Associate Recruiter"),                                # 0-2 -> doesn't fit
        job("Talent Acquisition Partner"),                         # nothing stated -> kept
    ]
    kept, rejected = ExperienceFilter().apply(jobs, criteria)
    assert [(k.title, k.description) for k in kept] == [
        ("Lead Recruiter", None), ("Recruiter", "4-6 years of recruiting experience"),
        ("Talent Acquisition Partner", None)]
    assert len(rejected) == 2


def test_sde_level_rule_off_with_sde_null():
    # Adobe MTS-2 is SDE-2 (2-5 yrs): at 1 yr it passes only via the level rule.
    mts2 = job("Member of Technical Staff 2", company="Adobe")
    with_rule = SearchCriteria.build(["a"], posted_within_hours=None, experience={"years": 1})
    without = SearchCriteria.build(["a"], posted_within_hours=None, experience={"years": 1, "sde": None})
    assert len(ExperienceFilter().apply([mts2], with_rule)[0]) == 1
    assert ExperienceFilter().apply([mts2], without)[0] == []


# --------------------------------------------------------------------------- #
# Wording
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("role, noun", [
    (None, "software"), ("software engineer", "software"), ("backend", "software"),
    ("sde", "software"), ("recruiter", "recruiter"), ("Recruiter", "recruiter"),
])
def test_role_noun(role, noun):
    assert role_noun(role) == noun


def test_summary_wording_follows_role():
    from tests.test_report_seen import TestCompanySummary

    report = TestCompanySummary()._report()
    sw = dict(company_summary(report, hours=24, location="India", experience=3, role="software engineer"))
    default = dict(company_summary(report, hours=24, location="India", experience=3))
    assert sw == default
    assert sw["Coinbase"] == "1 new posting, none are software roles"
    rec = dict(company_summary(report, hours=24, location="India", experience=5, role="recruiter"))
    assert rec["Coinbase"] == "1 new posting, none are recruiter roles"
    assert rec["Rubrik"] == "2 new recruiter roles, none in India"


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #

MD = """## FaangScout: 1 new opening

role: **recruiter** · in **India**

| Company | Role | Link |
|---|---|---|
| Acme | Talent \\| Partner | [open](https://x/1?a=1&b=2) |

**No match today (1):**

- **Stripe**: no new postings in the last 24h
"""


def test_markdown_to_html_table_and_lists():
    out = markdown_to_html(MD)
    assert "<h2>FaangScout: 1 new opening</h2>" in out
    assert "<table" in out and "<th" in out and ">Talent | Partner</td>" in out
    assert '<a href="https://x/1?a=1&amp;b=2">open</a>' in out
    assert "<li><b>Stripe</b>: no new postings in the last 24h</li>" in out


def test_build_message():
    msg = build_message("a@example.com, b@example.com", "Subj", MD, sender="me@example.com")
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Subject"] == "Subj" and msg["From"] == "me@example.com"
    kinds = [p.get_content_type() for p in msg.iter_parts()]
    assert kinds == ["text/plain", "text/html"]


def test_send_requires_credentials():
    with pytest.raises(EmailConfigError, match="SMTP_USERNAME"):
        send_markdown_email("a@example.com", "s", MD, env={})


def test_send_uses_starttls_and_login():
    env = {"SMTP_USERNAME": "me@gmail.com", "SMTP_PASSWORD": "app pass"}
    with mock.patch("smtplib.SMTP") as smtp_cls:
        send_markdown_email("her@example.com", "s", MD, env=env)
    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=60)
    smtp = smtp_cls.return_value.__enter__.return_value
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("me@gmail.com", "app pass")
    sent = smtp.send_message.call_args.args[0]
    assert sent["To"] == "her@example.com"


def test_cli_emails_only_when_there_are_matches(tmp_path, monkeypatch):
    from faangscout import cli
    from faangscout.models import ScoredJob, ScoutReport

    sent = []
    monkeypatch.setattr(cli, "send_markdown_email", lambda to, subject, body: sent.append((to, subject, body)))
    reports = iter([ScoutReport(jobs=[ScoredJob(job("Technical Recruiter"))]), ScoutReport()])
    monkeypatch.setattr(cli, "scout", lambda *a, **k: next(reports))
    argv = ["--companies", "Acme", "--role", "recruiter", "--email-to", "her@example.com", "--markdown"]
    assert cli.main(argv) == 0
    assert cli.main(argv) == 0
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "her@example.com"
    assert subject == "FaangScout: 1 new recruiter opening (last 24h)"
    assert "Technical Recruiter" in body


def test_his_workflow_does_not_email():
    text = (ROOT / ".github" / "workflows" / "scout.yml").read_text()
    assert "--email-to" not in text and "SMTP_" not in text
    rec = (ROOT / ".github" / "workflows" / "scout-recruiter.yml").read_text()
    assert "profiles/recruiter.yaml" in rec and ".faangscout-recruiter" in rec
    assert "bhatt" not in rec  # the address is a secret, not in the repo
