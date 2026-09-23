from datetime import UTC, datetime, timedelta

from faangscout.models import Job, Precision, ScoredJob, ScoutReport, SourceReport
from faangscout.report import format_age, render_markdown
from faangscout.seen import SeenStore, job_key

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def job(title="Engineer", *, ext="1", hours_ago=3.0, precision=Precision.EXACT, **kw):
    return Job(company="Acme", title=title, url=f"https://x/{ext}", source="greenhouse",
               external_id=ext, posted_at=NOW - timedelta(hours=hours_ago), precision=precision, **kw)


class TestFormatAge:
    def test_exact_hours(self):
        assert format_age(job(hours_ago=5), now=NOW) == "5h ago"

    def test_date_only_uses_calendar_days(self):
        assert format_age(job(hours_ago=2, precision=Precision.DATE_ONLY), now=NOW) == "today"
        assert format_age(job(hours_ago=20, precision=Precision.DATE_ONLY), now=NOW) == "yesterday"

    def test_undated(self):
        assert format_age(Job(company="A", title="t", url="u", source="s"), now=NOW) == "date unknown"


class TestMarkdown:
    def test_table_and_notes(self):
        report = ScoutReport(
            jobs=[ScoredJob(job(title="Backend | Payments", locations=("Remote",)))],
            sources=[SourceReport("Acme", "greenhouse:acme", fetched=3),
                     SourceReport("Adobe", "workday:adobe", error="HTTP 404")],
            unresolved=["Apple"],
        )
        md = render_markdown(report, role="software engineer", hours=24, new_only=True)
        assert "1 new opening" in md and "openings" not in md.split("\n")[0]
        assert "Backend \\| Payments" in md  # pipe escaped, table intact
        assert "[open](https://x/1)" in md
        assert "Checked 1/2 boards" in md
        assert "Adobe" in md and "HTTP 404" in md
        assert "Not covered yet (1):** Apple" in md

    def test_empty(self):
        md = render_markdown(ScoutReport())
        assert "0 openings" in md
        assert "No matching openings" in md


class TestSeenStore:
    def test_filters_and_persists(self, tmp_path):
        path = tmp_path / "state" / "seen.json"
        store = SeenStore(path)
        a, b = job(ext="a"), job(ext="b")
        assert store.filter_new([a, b]) == [a, b]
        store.mark([a])
        store.save()

        reloaded = SeenStore(path)
        assert reloaded.filter_new([a, b]) == [b]

    def test_key_falls_back_to_url(self):
        assert job_key(Job(company="A", title="t", url="https://u", source="s")) == "https://u"

    def test_old_entries_expire(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore(path)
        store.mark([job(ext="old")], now=NOW - timedelta(days=40))
        store.mark([job(ext="new")], now=NOW)
        store.save(now=NOW)
        assert SeenStore(path).filter_new([job(ext="old"), job(ext="new")]) == [job(ext="old")]

    def test_corrupt_file_is_treated_as_empty(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text("{not json")
        assert SeenStore(path).filter_new([job()]) == [job()]


def test_max_rows_truncates_and_points_to_full_list():
    report = ScoutReport(jobs=[ScoredJob(job(ext=str(i))) for i in range(5)])
    md = render_markdown(report, max_rows=2, more_link="https://run/1")
    assert md.count("[open](") == 2
    assert "…and 3 more - [full list](https://run/1)" in md
    assert "5 openings" in md  # heading still counts everything


class TestCompanySummary:
    """One line per company without rows, naming where its jobs dropped out."""

    @staticmethod
    def _report():
        from faangscout.experience import assess
        from faangscout.models import CompanySource, Rejection, ResolvedCompany
        from dataclasses import replace

        def co(name, *, note="", resolved=True):
            sources = (CompanySource("greenhouse", {"board": name}),) if resolved else ()
            return ResolvedCompany(query=name, name=name, sources=sources, note=note)

        def j(company, title="Software Engineer", **kw):
            return replace(job(title, **kw), company=company)

        senior = j("Adobe", "Senior Software Engineer", description="Requires 5+ years of experience")
        senior = replace(senior, experience=assess(senior))
        return ScoutReport(
            companies=[co("Acme"), co("Stripe"), co("Rippling"), co("Coinbase"), co("Rubrik"),
                       co("Adobe"), co("PhonePe"), co("Airbnb"), co("Walmart", note="search API too brittle", resolved=False)],
            jobs=[ScoredJob(j("Acme"))],
            sources=[SourceReport(n, f"greenhouse:{n}", fetched=5) for n in
                     ("Acme", "Stripe", "Rippling", "Coinbase", "Rubrik", "Adobe", "PhonePe")]
                    + [SourceReport("Airbnb", "greenhouse:airbnb", error="HTTP 404 Not Found")],
            rejections=[
                Rejection(j("Stripe"), "posted_within", "posted 50h ago, outside window"),
                Rejection(replace(j("Rippling"), posted_at=None, precision=Precision.FIRST_SEEN),
                          "posted_within", "no parseable post date"),
                Rejection(j("Coinbase", "Account Executive"), "role", "no"),
                Rejection(j("Rubrik"), "location", "'Palo Alto' is not in India"),
                Rejection(j("Rubrik", ext="2"), "location", "'Remote - US' is not in India"),
                Rejection(senior, "experience", "requires 5+ yrs"),
                Rejection(j("PhonePe"), "already_sent", "reported in an earlier run"),
            ],
        )

    def test_every_outcome(self):
        from faangscout.report import company_summary

        lines = dict(company_summary(self._report(), hours=24, location="India", experience=3))
        assert "Acme" not in lines  # has a row
        assert lines["Stripe"] == "no new postings in the last 24h"
        assert lines["Rippling"].startswith("no new postings in the last 24h (this board shows no dates")
        assert lines["Coinbase"] == "1 new posting, none software roles"
        assert lines["Rubrik"] == "2 new software roles, none in India"
        assert lines["Adobe"] == "1 new India software role, doesn't fit 3 yrs (needs 5+ yrs)"
        assert lines["PhonePe"] == "1 matching role, already sent in an earlier email"
        assert lines["Airbnb"].startswith("couldn't check the board (HTTP 404")
        assert lines["Walmart"] == "not covered (search API too brittle)"

    def test_rendered_in_the_email(self):
        md = render_markdown(self._report(), hours=24, location="India", experience=3, new_only=True)
        assert "**No match today (8):**" in md
        assert "- **Walmart**: not covered (search API too brittle)" in md
        assert "Not covered yet" not in md
