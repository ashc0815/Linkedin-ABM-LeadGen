"""Tests for daily_actions module and pipeline daily/stats CLI commands."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.daily_actions import (
    generate_daily_report,
    generate_pipeline_stats,
    generate_weekly_plan,
)
from src.models import Company, Contact

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _co(**kw) -> Company:
    defaults = {
        "company_name": "TestCo",
        "linkedin_url": "https://linkedin.com/company/testco/",
        "industry": "Manufacturing",
        "lead_score": 50,
    }
    defaults.update(kw)
    return Company(**defaults)


def _ct(**kw) -> Contact:
    defaults = {
        "name": "Jane",
        "title": "CFO",
        "company_name": "TestCo",
        "linkedin_url": "https://linkedin.com/in/jane/",
        "contact_type": "CFO",
        "flow_type": "re_activation",
        "dm_status": "not_started",
        "lead_score": 50,
    }
    defaults.update(kw)
    return Contact(**defaults)


def _make_settings(**overrides):
    s = MagicMock()
    s.feishu_app_id = "fid"
    s.feishu_app_secret = "fsec"
    s.feishu_bitable_app_token = "fatok"
    s.feishu_companies_table_id = "tbl_co"
    s.feishu_contacts_table_id = "tbl_ct"
    s.unipile_api_key = ""
    s.unipile_dsn = ""
    s.unipile_account_id = ""
    s.apify_api_token = ""
    s.anthropic_api_key = ""
    s.brave_search_api_key = ""
    s.warmup_mode = False
    s.warmup_daily_limit = 10
    s.meeting_pipeline_value = 50000
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# generate_daily_report
# ---------------------------------------------------------------------------


class TestGenerateDailyReport:
    def test_basic_report_structure(self):
        contacts = [_ct()]
        companies = [_co()]
        report = generate_daily_report(contacts, companies)
        assert report["date"] == date.today()
        assert report["total_contacts"] == 1
        assert report["total_companies"] == 1
        assert isinstance(report["due_today"], list)
        assert isinstance(report["steps"], list)

    def test_due_today(self):
        today = date.today()
        ct_due = _ct(
            name="Due",
            next_touch_date=datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
            dm_status="day1_sent",
        )
        ct_future = _ct(
            name="Future",
            next_touch_date=datetime.now(timezone.utc) + timedelta(days=5),
            dm_status="day1_sent",
        )
        report = generate_daily_report([ct_due, ct_future], [])
        assert len(report["due_today"]) == 1
        assert report["due_today"][0].name == "Due"

    def test_queued_contacts(self):
        ct = _ct(dm_status="day1_queued")
        report = generate_daily_report([ct], [])
        assert len(report["queued"]) == 1

    def test_pending_acceptances(self):
        ct = _ct(dm_status="day1_sent", flow_type="cold_new")
        report = generate_daily_report([ct], [])
        assert len(report["pending_acceptances"]) == 1

    def test_sent_today(self):
        ct = _ct(last_touch_date=datetime.now(timezone.utc))
        report = generate_daily_report([ct], [])
        assert len(report["sent_today"]) == 1

    def test_need_drafts(self):
        ct = _ct(dm_status="not_started", lead_score=80)
        report = generate_daily_report([ct], [])
        assert len(report["need_drafts"]) == 1

    def test_replied_and_meetings(self):
        ct_replied = _ct(name="Replied", dm_status="replied")
        ct_meeting = _ct(name="Meeting", dm_status="meeting_booked")
        report = generate_daily_report([ct_replied, ct_meeting], [])
        assert len(report["replied"]) == 1
        assert len(report["meetings"]) == 1

    def test_suggested_steps_order(self):
        contacts = [
            _ct(name="Pending", dm_status="day1_sent", flow_type="cold_new"),
            _ct(name="Replied", dm_status="replied"),
            _ct(name="NeedDraft", dm_status="not_started", lead_score=80),
            _ct(name="Queued", dm_status="day1_queued"),
        ]
        report = generate_daily_report(contacts, [])
        steps = report["steps"]
        # check-acceptances should come first (priority 1)
        assert steps[0]["command"] == "pipeline check-acceptances"
        # Manual reply should come second (priority 2)
        assert steps[1]["command"] == "(manual)"

    def test_empty_pipeline(self):
        report = generate_daily_report([], [])
        assert report["total_contacts"] == 0
        assert report["steps"] == []


# ---------------------------------------------------------------------------
# generate_weekly_plan
# ---------------------------------------------------------------------------


class TestGenerateWeeklyPlan:
    def test_returns_7_days(self):
        plan = generate_weekly_plan([])
        assert len(plan) == 7

    def test_contacts_assigned_to_correct_day(self):
        today = date.today()
        tomorrow = today + timedelta(days=1)
        ct = _ct(
            next_touch_date=datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc),
            dm_status="day1_sent",
        )
        plan = generate_weekly_plan([ct], start_date=today)
        assert plan[0]["count"] == 0  # today
        assert plan[1]["count"] == 1  # tomorrow

    def test_by_touch_breakdown(self):
        today = date.today()
        ct = _ct(
            next_touch_date=datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
            dm_status="day1_sent",
        )
        plan = generate_weekly_plan([ct], start_date=today)
        assert plan[0]["by_touch"].get("day7") == 1

    def test_weekday_labels(self):
        plan = generate_weekly_plan([])
        weekdays = [p["weekday"] for p in plan]
        assert all(wd in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") for wd in weekdays)


# ---------------------------------------------------------------------------
# generate_pipeline_stats
# ---------------------------------------------------------------------------


class TestGeneratePipelineStats:
    def test_basic_stats(self):
        contacts = [
            _ct(flow_type="re_activation", dm_status="not_started"),
            _ct(flow_type="cold_new", dm_status="day1_sent", touch_count=1),
            _ct(flow_type="re_activation", dm_status="replied", touch_count=2),
            _ct(flow_type="cold_new", dm_status="meeting_booked", touch_count=3),
        ]
        companies = [
            _co(sap_user="Yes", enrichment_signals={"x": 1}),
            _co(sap_user="No", uses_concur="Yes"),
        ]
        stats = generate_pipeline_stats(contacts, companies)

        assert stats["total_contacts"] == 4
        assert stats["total_companies"] == 2
        assert stats["touched"] == 3  # touch_count > 0
        assert stats["replied"] == 1
        assert stats["meetings"] == 1
        assert stats["sap_confirmed"] == 1
        assert stats["concur_confirmed"] == 1
        assert stats["enriched_companies"] == 1
        assert stats["reply_rate"] > 0
        assert stats["meeting_rate"] > 0

    def test_status_distribution(self):
        contacts = [
            _ct(dm_status="not_started"),
            _ct(dm_status="not_started"),
            _ct(dm_status="day1_sent"),
        ]
        stats = generate_pipeline_stats(contacts, [])
        assert stats["status_dist"]["not_started"] == 2
        assert stats["status_dist"]["day1_sent"] == 1

    def test_flow_distribution(self):
        contacts = [
            _ct(flow_type="re_activation"),
            _ct(flow_type="cold_new"),
            _ct(flow_type="cold_new"),
        ]
        stats = generate_pipeline_stats(contacts, [])
        assert stats["flow_dist"]["re_activation"] == 1
        assert stats["flow_dist"]["cold_new"] == 2

    def test_industry_distribution(self):
        companies = [
            _co(industry="Manufacturing"),
            _co(industry="Manufacturing"),
            _co(industry="Mining"),
        ]
        stats = generate_pipeline_stats([], companies)
        assert stats["industry_dist"]["Manufacturing"] == 2
        assert stats["industry_dist"]["Mining"] == 1

    def test_empty_data(self):
        stats = generate_pipeline_stats([], [])
        assert stats["total_contacts"] == 0
        assert stats["total_companies"] == 0
        assert stats["reply_rate"] == 0
        assert stats["avg_company_score"] == 0

    def test_score_averages(self):
        contacts = [_ct(lead_score=40), _ct(lead_score=60)]
        companies = [_co(lead_score=30), _co(lead_score=70)]
        stats = generate_pipeline_stats(contacts, companies)
        assert stats["avg_contact_score"] == 50
        assert stats["avg_company_score"] == 50
        assert stats["max_contact_score"] == 60
        assert stats["max_company_score"] == 70


# ---------------------------------------------------------------------------
# CLI: pipeline daily
# ---------------------------------------------------------------------------


class TestPipelineDailyCLI:
    def test_help(self):
        from src.cli import app
        result = runner.invoke(app, ["pipeline", "daily", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output.lower()

    def test_aborts_without_feishu(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "daily"])
        assert result.exit_code == 1

    def test_renders_dashboard(self):
        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli._load_all_contacts_and_companies") as mock_load:
            mock_settings.return_value = _make_settings()
            mock_load.return_value = (
                [
                    _ct(dm_status="day1_queued", lead_score=80),
                    _ct(name="Cold", flow_type="cold_new", dm_status="day1_sent"),
                ],
                [_co()],
            )
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "daily"])

        assert result.exit_code == 0
        assert "DASHBOARD" in result.output or "PIPELINE" in result.output
        assert "RE-ACTIVATION" in result.output
        assert "COLD NEW" in result.output

    def test_week_flag_shows_plan(self):
        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli._load_all_contacts_and_companies") as mock_load:
            mock_settings.return_value = _make_settings()
            mock_load.return_value = ([], [])
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "daily", "--week"])

        assert result.exit_code == 0
        assert "7-Day" in result.output or "Forward Plan" in result.output


# ---------------------------------------------------------------------------
# CLI: pipeline stats
# ---------------------------------------------------------------------------


class TestPipelineStatsCLI:
    def test_help(self):
        from src.cli import app
        result = runner.invoke(app, ["pipeline", "stats", "--help"])
        assert result.exit_code == 0
        assert "funnel" in result.output.lower() or "statistics" in result.output.lower()

    def test_aborts_without_feishu(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "stats"])
        assert result.exit_code == 1

    def test_renders_stats(self):
        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli._load_all_contacts_and_companies") as mock_load:
            mock_settings.return_value = _make_settings()
            mock_load.return_value = (
                [
                    _ct(dm_status="replied", touch_count=2, flow_type="re_activation",
                        last_touch_date=datetime.now(timezone.utc)),
                    _ct(name="Bob", dm_status="meeting_booked", touch_count=3, flow_type="cold_new",
                        last_touch_date=datetime.now(timezone.utc)),
                ],
                [_co(sap_user="Yes", enrichment_signals={"x": 1})],
            )
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "stats"])

        assert result.exit_code == 0
        assert "FUNNEL" in result.output
        assert "CONVERSION" in result.output
        assert "AUD" in result.output
        assert "50,000" in result.output or "50000" in result.output

    def test_pipeline_value_calculation(self):
        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli._load_all_contacts_and_companies") as mock_load:
            mock_settings.return_value = _make_settings(meeting_pipeline_value=75000)
            mock_load.return_value = (
                [
                    _ct(dm_status="meeting_booked", touch_count=1,
                        last_touch_date=datetime.now(timezone.utc)),
                    _ct(name="B", dm_status="meeting_booked", touch_count=1,
                        last_touch_date=datetime.now(timezone.utc)),
                ],
                [],
            )
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "stats"])

        assert result.exit_code == 0
        # 2 meetings × 75K = 150K
        assert "150,000" in result.output or "150000" in result.output

    def test_period_week_filters(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        recent = datetime.now(timezone.utc) - timedelta(days=3)

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli._load_all_contacts_and_companies") as mock_load:
            mock_settings.return_value = _make_settings()
            mock_load.return_value = (
                [
                    _ct(name="Old", last_touch_date=old, touch_count=1),
                    _ct(name="Recent", last_touch_date=recent, touch_count=1),
                ],
                [_co()],
            )
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "stats", "--period", "week"])

        assert result.exit_code == 0
        # Only 1 contact should be in the filtered set (Recent)
        assert "1" in result.output  # total touched
