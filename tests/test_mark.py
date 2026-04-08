"""Tests for pipeline mark subcommands (sent, replied, rejected, meeting)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _make_settings():
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
    return s


def _contact_record(name="Jane Smith", dm_status="day1_queued", company="Acme", flow_type="re_activation"):
    return {
        "record_id": "r_jane",
        "fields": {
            "姓名": name,
            "职位": "CFO",
            "公司名称": company,
            "LinkedIn URL": "https://linkedin.com/in/jane/",
            "联系人类型": "CFO",
            "Flow类型": flow_type,
            "DM状态": dm_status,
            "触达次数": 1,
            "Lead评分": 80,
            "DM草稿": json.dumps([
                {"touch": "day1", "draft": "Hi Jane!", "generated_at": "2024-01-01", "sent_at": None}
            ]),
        },
    }


def _company_record(name="Acme"):
    return {
        "record_id": "r_acme",
        "fields": {
            "公司名称": name,
            "LinkedIn URL": "https://linkedin.com/company/acme/",
            "行业": "Manufacturing",
            "触达状态": "未触达",
            "来源": "Apify",
            "Lead评分": 50,
        },
    }


# ---------------------------------------------------------------------------
# mark sent
# ---------------------------------------------------------------------------


class TestMarkSent:
    def test_marks_day1_sent(self):
        update_calls = []

        def mock_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "record_id": record_id, "fields": fields})
            return {}

        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[_contact_record()]), \
                 patch.object(BitableClient, "update_record", side_effect=mock_update):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "sent",
                    "--contact", "Jane Smith",
                    "--touch", "day1",
                ])

        assert result.exit_code == 0
        assert "Day 1" in result.output
        assert "Jane Smith" in result.output
        assert "Next touch" in result.output
        # Verify Bitable update
        from src.bitable_client import CONTACT_FIELD_MAP
        assert update_calls[0]["fields"][CONTACT_FIELD_MAP["dm_status"]] == "day1_sent"

    def test_day21_no_next_touch(self):
        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[_contact_record()]), \
                 patch.object(BitableClient, "update_record", return_value={}):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "sent",
                    "--contact", "Jane Smith",
                    "--touch", "day21",
                ])

        assert result.exit_code == 0
        assert "Day 21" in result.output

    def test_invalid_touch_rejects(self):
        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.cli import app
            result = runner.invoke(app, [
                "pipeline", "mark", "sent",
                "--contact", "Jane Smith",
                "--touch", "day99",
            ])
        assert result.exit_code == 1

    def test_contact_not_found(self):
        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[]):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "sent",
                    "--contact", "Nobody",
                    "--touch", "day1",
                ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# mark replied
# ---------------------------------------------------------------------------


class TestMarkReplied:
    def test_marks_replied_with_summary(self):
        update_calls = []

        def track_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "fields": fields})
            return {}

        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient
            co_rec = _company_record()

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [_contact_record()]  # contact lookup
                return [co_rec]  # company lookup

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", side_effect=track_update):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "replied",
                    "--contact", "Jane Smith",
                    "--summary", "Interested, wants to chat next week",
                ])

        assert result.exit_code == 0
        assert "replied" in result.output.lower()
        assert "meeting" in result.output.lower()
        # Should have 2 updates: contact + company
        assert len(update_calls) == 2

    def test_updates_company_status(self):
        update_calls = []

        def track_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "record_id": record_id, "fields": fields})
            return {}

        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient, COMPANY_FIELD_MAP

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [_contact_record()]
                return [_company_record()]

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", side_effect=track_update):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "replied",
                    "--contact", "Jane Smith",
                    "--summary", "Interested",
                ])

        # Company update should set outreach_status to 已回复
        co_update = update_calls[-1]
        assert co_update["fields"][COMPANY_FIELD_MAP["outreach_status"]] == "已回复"


# ---------------------------------------------------------------------------
# mark rejected
# ---------------------------------------------------------------------------


class TestMarkRejected:
    def test_marks_rejected(self):
        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [_contact_record()]
                if call_count[0] == 2:
                    return [_company_record()]  # company lookup
                # Contacts at company (for only_if_all check)
                return [_contact_record(dm_status="rejected")]

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", return_value={}):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "rejected",
                    "--contact", "Jane Smith",
                ])

        assert result.exit_code == 0
        assert "opted out" in result.output.lower()

    def test_does_not_update_company_if_other_contacts_active(self):
        """Company status stays if other contacts are still active."""
        update_calls = []

        def track_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "record_id": record_id})
            return {}

        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [_contact_record()]  # contact lookup
                if call_count[0] == 2:
                    return [_company_record()]  # company lookup
                # Other contacts at company: one rejected, one active
                return [
                    _contact_record(name="Jane Smith", dm_status="rejected"),
                    _contact_record(name="Bob Lee", dm_status="day1_sent"),
                ]

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", side_effect=track_update):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "rejected",
                    "--contact", "Jane Smith",
                ])

        assert result.exit_code == 0
        # Only 1 update (contact), not 2 (company should NOT be updated)
        assert len(update_calls) == 1


# ---------------------------------------------------------------------------
# mark meeting
# ---------------------------------------------------------------------------


class TestMarkMeeting:
    def test_marks_meeting_booked(self):
        update_calls = []

        def track_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "record_id": record_id, "fields": fields})
            return {}

        with patch("src.cli.load_settings", return_value=_make_settings()):
            from src.bitable_client import BitableClient, CONTACT_FIELD_MAP, COMPANY_FIELD_MAP

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [_contact_record()]
                return [_company_record()]

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", side_effect=track_update):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "mark", "meeting",
                    "--contact", "Jane Smith",
                ])

        assert result.exit_code == 0
        assert "Meeting booked" in result.output
        assert "Jane Smith" in result.output
        # Contact update
        assert update_calls[0]["fields"][CONTACT_FIELD_MAP["dm_status"]] == "meeting_booked"
        # Company update
        assert update_calls[1]["fields"][COMPANY_FIELD_MAP["outreach_status"]] == "已约meeting"
