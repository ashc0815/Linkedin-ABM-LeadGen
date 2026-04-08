"""Tests for pipeline check-acceptances command."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _make_settings(**overrides):
    s = MagicMock()
    s.feishu_app_id = "fid"
    s.feishu_app_secret = "fsec"
    s.feishu_bitable_app_token = "fatok"
    s.feishu_companies_table_id = "tbl_co"
    s.feishu_contacts_table_id = "tbl_ct"
    s.unipile_api_key = "ukey"
    s.unipile_dsn = "https://api.unipile.test"
    s.unipile_account_id = "uacc"
    s.apify_api_token = ""
    s.anthropic_api_key = ""
    s.brave_search_api_key = ""
    s.warmup_mode = False
    s.warmup_daily_limit = 10
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _contact_record(name, provider_id, days_ago, chat_id=None):
    """Build a cold_new day1_sent contact record with last_touch_date N days ago."""
    sent_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    # Bitable stores dates as ms timestamps
    sent_ms = int(sent_at.timestamp() * 1000)
    fields = {
        "姓名": name,
        "职位": "CFO",
        "公司名称": "TestCo",
        "LinkedIn URL": f"https://linkedin.com/in/{name.lower().replace(' ', '')}/",
        "联系人类型": "CFO",
        "Flow类型": "cold_new",
        "DM状态": "day1_sent",
        "Provider ID": provider_id,
        "触达次数": 1,
        "Lead评分": 50,
        "上次触达": sent_ms,
    }
    if chat_id:
        fields["Chat ID"] = chat_id
    return {"record_id": f"r_{name.lower().replace(' ', '_')}", "fields": fields}


# ---------------------------------------------------------------------------
# CLI basics
# ---------------------------------------------------------------------------


class TestCheckAcceptancesCLI:
    def test_help(self):
        from src.cli import app
        result = runner.invoke(app, ["pipeline", "check-acceptances", "--help"])
        assert result.exit_code == 0
        assert "withdraw" in result.output.lower()

    def test_aborts_without_unipile(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(unipile_api_key="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "check-acceptances"])
        assert result.exit_code == 1
        assert "Unipile" in result.output

    def test_aborts_without_feishu(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "check-acceptances"])
        assert result.exit_code == 1

    def test_no_pending_contacts(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings()
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[]):
                from src.cli import app
                result = runner.invoke(app, ["pipeline", "check-acceptances"])
        assert result.exit_code == 0
        assert "No pending" in result.output


# ---------------------------------------------------------------------------
# Acceptance scenarios
# ---------------------------------------------------------------------------


class TestAcceptedConnections:
    def test_accepted_moves_to_day7_queue(self):
        """Connection accepted → dm_status = day7_queued."""
        record = _contact_record("Jane Smith", "pid_jane", days_ago=3)

        update_calls = []

        def mock_update(table_id, record_id, fields):
            update_calls.append({"table_id": table_id, "record_id": record_id, "fields": fields})
            return {}

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(BitableClient, "update_record", side_effect=mock_update), \
                 patch.object(UnipileClient, "check_is_connection", return_value=True), \
                 patch.object(UnipileClient, "get_messages", return_value=[]):

                from src.cli import app
                result = runner.invoke(app, ["pipeline", "check-acceptances"])

        assert result.exit_code == 0
        assert "Accepted: 1" in result.output
        assert "Day 7" in result.output
        # Verify the Bitable update set dm_status to day7_queued
        assert len(update_calls) == 1
        from src.bitable_client import CONTACT_FIELD_MAP
        status_col = CONTACT_FIELD_MAP["dm_status"]
        assert update_calls[0]["fields"][status_col] == "day7_queued"

    def test_accepted_with_reply_sets_replied(self):
        """Connection accepted + inbound message → dm_status = replied."""
        record = _contact_record("Bob Lee", "pid_bob", days_ago=5, chat_id="chat_bob")

        update_calls = []

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            inbound_messages = [
                {"sender_id": "pid_bob", "text": "Thanks for connecting!"},
            ]

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(BitableClient, "update_record", side_effect=lambda *a: update_calls.append(a) or {}), \
                 patch.object(UnipileClient, "check_is_connection", return_value=True), \
                 patch.object(UnipileClient, "get_messages", return_value=inbound_messages):

                from src.cli import app
                result = runner.invoke(app, ["pipeline", "check-acceptances"])

        assert result.exit_code == 0
        assert "Replied: 1" in result.output or "REPLIED" in result.output
        assert "operator" in result.output.lower()


# ---------------------------------------------------------------------------
# Waiting scenario
# ---------------------------------------------------------------------------


class TestWaitingConnections:
    def test_waiting_shows_days(self):
        """Not accepted + under threshold → just waiting."""
        record = _contact_record("Carol Wu", "pid_carol", days_ago=5)

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(UnipileClient, "check_is_connection", return_value=False):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "check-acceptances",
                    "--withdraw-after-days", "14",
                ])

        assert result.exit_code == 0
        assert "Waiting: 1" in result.output
        assert "5 days" in result.output or "5d" in result.output


# ---------------------------------------------------------------------------
# Withdrawal scenario
# ---------------------------------------------------------------------------


class TestWithdrawConnections:
    def test_withdraw_after_threshold(self):
        """Not accepted + over threshold → withdraw + reset."""
        record = _contact_record("Dave Brown", "pid_dave", days_ago=15)

        update_calls = []

        def mock_update(table_id, record_id, fields):
            update_calls.append(fields)
            return {}

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(BitableClient, "update_record", side_effect=mock_update), \
                 patch.object(UnipileClient, "check_is_connection", return_value=False), \
                 patch.object(UnipileClient, "find_invitation_for", return_value="inv_123"), \
                 patch.object(UnipileClient, "withdraw_invitation", return_value=True) as mock_withdraw:

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "check-acceptances",
                    "--withdraw-after-days", "14",
                ])

        assert result.exit_code == 0
        assert "Withdrawn: 1" in result.output
        mock_withdraw.assert_called_once_with("inv_123")

        # Verify contact was reset
        from src.bitable_client import CONTACT_FIELD_MAP
        status_col = CONTACT_FIELD_MAP["dm_status"]
        assert update_calls[0][status_col] == "not_started"

        # Verify reply_summary contains withdrawal note
        summary_col = CONTACT_FIELD_MAP["reply_summary"]
        assert "withdrawn" in update_calls[0].get(summary_col, "").lower()

    def test_withdraw_invitation_not_found(self):
        """Invitation not found → still reset status, note the issue."""
        record = _contact_record("Eve Jones", "pid_eve", days_ago=20)

        update_calls = []

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(BitableClient, "update_record", side_effect=lambda *a: update_calls.append(a) or {}), \
                 patch.object(UnipileClient, "check_is_connection", return_value=False), \
                 patch.object(UnipileClient, "find_invitation_for", return_value=None):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "check-acceptances",
                    "--withdraw-after-days", "14",
                ])

        assert result.exit_code == 0
        assert "Withdrawn: 1" in result.output or "Reset" in result.output

    def test_custom_withdraw_days(self):
        """--withdraw-after-days 7 → withdraw after 7 days."""
        record = _contact_record("Frank Lee", "pid_frank", days_ago=8)

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[record]), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "check_is_connection", return_value=False), \
                 patch.object(UnipileClient, "find_invitation_for", return_value="inv_x"), \
                 patch.object(UnipileClient, "withdraw_invitation", return_value=True):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "check-acceptances",
                    "--withdraw-after-days", "7",
                ])

        assert result.exit_code == 0
        assert "Withdrawn: 1" in result.output


# ---------------------------------------------------------------------------
# Mixed scenario
# ---------------------------------------------------------------------------


class TestMixedScenario:
    def test_mixed_accepted_waiting_withdrawn(self):
        """Multiple contacts with different states."""
        records = [
            _contact_record("Accepted Alice", "pid_alice", days_ago=3),     # will be accepted
            _contact_record("Waiting Bob", "pid_bob", days_ago=5),          # waiting
            _contact_record("Withdrawn Carol", "pid_carol", days_ago=16),   # withdrawn
        ]

        connection_map = {"pid_alice": True, "pid_bob": False, "pid_carol": False}

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=records), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "check_is_connection", side_effect=lambda pid: connection_map.get(pid, False)), \
                 patch.object(UnipileClient, "get_messages", return_value=[]), \
                 patch.object(UnipileClient, "find_invitation_for", return_value="inv_1"), \
                 patch.object(UnipileClient, "withdraw_invitation", return_value=True):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "check-acceptances",
                    "--withdraw-after-days", "14",
                ])

        assert result.exit_code == 0
        assert "Accepted: 1" in result.output
        assert "Waiting: 1" in result.output
        assert "Withdrawn: 1" in result.output
        assert "Checked 3" in result.output


# ---------------------------------------------------------------------------
# find_invitation_for unit test
# ---------------------------------------------------------------------------


class TestFindInvitationFor:
    def test_finds_matching_invitation(self):
        from src.unipile_client import UnipileClient
        client = UnipileClient(api_key="k", dsn="https://x", account_id="a")
        client._rate_limiter.wait = lambda: None

        invitations = [
            {"id": "inv_1", "provider_id": "pid_other"},
            {"id": "inv_2", "provider_id": "pid_target"},
        ]
        with patch.object(client, "check_pending_invitations", return_value=invitations):
            result = client.find_invitation_for("pid_target")
        assert result == "inv_2"

    def test_returns_none_when_not_found(self):
        from src.unipile_client import UnipileClient
        client = UnipileClient(api_key="k", dsn="https://x", account_id="a")
        client._rate_limiter.wait = lambda: None

        with patch.object(client, "check_pending_invitations", return_value=[]):
            result = client.find_invitation_for("pid_target")
        assert result is None

    def test_handles_nested_invitee_format(self):
        from src.unipile_client import UnipileClient
        client = UnipileClient(api_key="k", dsn="https://x", account_id="a")
        client._rate_limiter.wait = lambda: None

        invitations = [
            {"invitation_id": "inv_3", "invitee": {"provider_id": "pid_target"}},
        ]
        with patch.object(client, "check_pending_invitations", return_value=invitations):
            result = client.find_invitation_for("pid_target")
        assert result == "inv_3"
