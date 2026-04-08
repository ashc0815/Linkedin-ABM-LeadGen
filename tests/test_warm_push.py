"""Tests for pipeline warm and pipeline push-dms CLI commands."""

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


# ---------------------------------------------------------------------------
# pipeline warm
# ---------------------------------------------------------------------------


class TestPipelineWarm:
    def test_help(self):
        from src.cli import app
        result = runner.invoke(app, ["pipeline", "warm", "--help"])
        assert result.exit_code == 0
        assert "view profiles" in result.output.lower() or "Warm" in result.output

    def test_invalid_touch(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings()
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "warm", "--touch", "day99"])
        assert result.exit_code == 1

    def test_aborts_without_unipile(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(unipile_api_key="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "warm", "--touch", "day1"])
        assert result.exit_code == 1
        assert "Unipile" in result.output

    def test_aborts_without_feishu(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "warm", "--touch", "day1"])
        assert result.exit_code == 1

    def test_no_contacts_exits_gracefully(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings()
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[]):
                from src.cli import app
                result = runner.invoke(app, ["pipeline", "warm", "--touch", "day1"])
        assert result.exit_code == 0
        assert "No contacts" in result.output

    def test_warm_executes_view_and_like(self):
        """Integration: mocks Unipile, verifies view + like called."""
        contact_record = {
            "record_id": "r1",
            "fields": {
                "姓名": "Jane Smith",
                "职位": "CFO",
                "公司名称": "Acme",
                "LinkedIn URL": "https://linkedin.com/in/jane/",
                "联系人类型": "CFO",
                "Flow类型": "re_activation",
                "DM状态": "day1_queued",
                "Provider ID": "pid_jane",
                "触达次数": 0,
                "Lead评分": 50,
            },
        }

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()
            mock_time.sleep = MagicMock()  # skip delays

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[contact_record]), \
                 patch.object(UnipileClient, "view_profile", return_value=True) as mock_view, \
                 patch.object(UnipileClient, "get_recent_posts", return_value=[{"id": "post_1"}]), \
                 patch.object(UnipileClient, "react_to_post", return_value=True) as mock_react:

                from src.cli import app
                result = runner.invoke(app, ["pipeline", "warm", "--touch", "day1"])

        assert result.exit_code == 0
        mock_view.assert_called_once_with("pid_jane")
        mock_react.assert_called_once_with("post_1")
        assert "viewed" in result.output.lower() or "Warming complete" in result.output


# ---------------------------------------------------------------------------
# pipeline push-dms
# ---------------------------------------------------------------------------


class TestPipelinePushDMs:
    def test_help(self):
        from src.cli import app
        result = runner.invoke(app, ["pipeline", "push-dms", "--help"])
        assert result.exit_code == 0
        assert "queued" in result.output.lower() or "Send" in result.output

    def test_invalid_touch(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings()
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "push-dms", "--touch", "day99"])
        assert result.exit_code == 1

    def test_aborts_without_unipile(self):
        with patch("src.cli.load_settings") as mock:
            mock.return_value = _make_settings(unipile_api_key="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "push-dms", "--touch", "day1"])
        assert result.exit_code == 1

    def test_daily_limit_blocks(self):
        """If 80+ already sent today, refuse."""
        # 80 records with last_touch_date = today
        today_records = [{"record_id": f"r{i}", "fields": {}} for i in range(80)]

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()

            from src.bitable_client import BitableClient

            call_count = 0

            def mock_list(table_id, filter_formula=None, page_size=100):
                nonlocal call_count
                call_count += 1
                # First call is the daily count check
                if call_count == 1:
                    return today_records
                return []

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 1
        assert "limit" in result.output.lower() or "80" in result.output

    def test_no_queued_contacts(self):
        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings()
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[]):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])
        assert result.exit_code == 0
        assert "No contacts" in result.output

    def test_push_sends_connection_request_for_cold_day1(self):
        """Cold new day1 → send_connection_request, not send_message."""
        import json

        contact_fields = {
            "姓名": "Bob Lee",
            "职位": "Finance Director",
            "公司名称": "TestCorp",
            "LinkedIn URL": "https://linkedin.com/in/bob/",
            "联系人类型": "Finance Director",
            "Flow类型": "cold_new",
            "DM状态": "day1_queued",
            "Provider ID": "pid_bob",
            "触达次数": 0,
            "Lead评分": 40,
            "DM草稿": json.dumps([{"touch": "day1", "draft": "Hi Bob!", "generated_at": "2024-01-01", "sent_at": None}]),
        }
        contact_record = {"record_id": "r_bob", "fields": contact_fields}
        company_record = {
            "record_id": "r_co",
            "fields": {"公司名称": "TestCorp", "LinkedIn URL": "https://li.com/co/tc/", "行业": "Other", "触达状态": "未触达", "来源": "Apify", "Lead评分": 0},
        }

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()
            mock_time.sleep = MagicMock()

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            list_call = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                list_call[0] += 1
                if list_call[0] == 1:
                    return []  # daily count check → 0 sent today
                if list_call[0] == 2:
                    return [contact_record]  # queued contacts
                if list_call[0] == 3:
                    return [company_record]  # company lookup
                return []

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "send_connection_request", return_value={"id": "chat_new"}) as mock_conn, \
                 patch.object(UnipileClient, "send_message") as mock_msg:

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 0
        mock_conn.assert_called_once_with("pid_bob", note="Hi Bob!")
        mock_msg.assert_not_called()
        assert "1 sent" in result.output

    def test_push_sends_message_for_reactivation(self):
        """Re-activation → send_message into existing chat."""
        import json

        contact_fields = {
            "姓名": "Alice Chen",
            "职位": "CFO",
            "公司名称": "BHP",
            "LinkedIn URL": "https://linkedin.com/in/alice/",
            "联系人类型": "CFO",
            "Flow类型": "re_activation",
            "DM状态": "day1_queued",
            "Provider ID": "pid_alice",
            "Chat ID": "chat_alice",
            "触达次数": 0,
            "Lead评分": 80,
            "DM草稿": json.dumps([{"touch": "day1", "draft": "Hey Alice!", "generated_at": "2024-01-01", "sent_at": None}]),
        }
        contact_record = {"record_id": "r_alice", "fields": contact_fields}

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()
            mock_time.sleep = MagicMock()

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            list_call = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                list_call[0] += 1
                if list_call[0] == 1:
                    return []  # daily count
                if list_call[0] == 2:
                    return [contact_record]
                return []  # companies

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "send_message", return_value={"id": "msg_1"}) as mock_msg, \
                 patch.object(UnipileClient, "send_connection_request") as mock_conn:

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 0
        mock_msg.assert_called_once_with("chat_alice", "Hey Alice!")
        mock_conn.assert_not_called()
        assert "1 sent" in result.output

    def test_cold_day7_uses_send_message(self):
        """cold_new day7+ → send_message (connection was already accepted)."""
        import json

        contact_fields = {
            "姓名": "Carol Wu",
            "职位": "Head of Finance",
            "公司名称": "Rio",
            "LinkedIn URL": "https://linkedin.com/in/carol/",
            "联系人类型": "Head of Finance",
            "Flow类型": "cold_new",
            "DM状态": "day7_queued",
            "Provider ID": "pid_carol",
            "Chat ID": "chat_carol",
            "触达次数": 1,
            "Lead评分": 60,
            "DM草稿": json.dumps([
                {"touch": "day1", "draft": "Sent", "generated_at": "2024-01-01", "sent_at": "2024-01-01"},
                {"touch": "day7", "draft": "Day 7 follow-up", "generated_at": "2024-01-08", "sent_at": None},
            ]),
        }
        contact_record = {"record_id": "r_carol", "fields": contact_fields}

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()
            mock_time.sleep = MagicMock()

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            list_call = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                list_call[0] += 1
                if list_call[0] == 1:
                    return []  # daily count
                if list_call[0] == 2:
                    return [contact_record]
                return []

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "send_message", return_value={"id": "m1"}) as mock_msg, \
                 patch.object(UnipileClient, "send_connection_request") as mock_conn:

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day7", "--confirm-all",
                ])

        assert result.exit_code == 0
        mock_msg.assert_called_once_with("chat_carol", "Day 7 follow-up")
        mock_conn.assert_not_called()

    def test_429_stops_session(self):
        """Unipile 429 → immediately stop, report remaining queue."""
        import json
        from src.unipile_client import UnipileRateLimitError

        contact_fields = {
            "姓名": "Eve Jones",
            "职位": "CFO",
            "公司名称": "Acme",
            "LinkedIn URL": "https://linkedin.com/in/eve/",
            "联系人类型": "CFO",
            "Flow类型": "re_activation",
            "DM状态": "day1_queued",
            "Provider ID": "pid_eve",
            "Chat ID": "chat_eve",
            "触达次数": 0,
            "Lead评分": 90,
            "DM草稿": json.dumps([{"touch": "day1", "draft": "Hi Eve!", "generated_at": "2024-01-01", "sent_at": None}]),
        }
        contact_record = {"record_id": "r_eve", "fields": contact_fields}

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()
            mock_time.sleep = MagicMock()

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            list_call = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                list_call[0] += 1
                if list_call[0] == 1:
                    return []  # daily count
                if list_call[0] == 2:
                    return [contact_record]  # queued contacts
                return []  # companies

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(UnipileClient, "send_message", side_effect=UnipileRateLimitError("429")):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 0
        assert "429" in result.output or "rate limit" in result.output.lower()
        assert "0 sent" in result.output

    def test_warmup_mode_reduces_limit(self):
        """WARMUP_MODE=true → daily limit is warmup_daily_limit (10)."""
        # 10 records already sent today → should block
        today_records = [{"record_id": f"r{i}", "fields": {}} for i in range(10)]

        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings(warmup_mode=True, warmup_daily_limit=10)

            from src.bitable_client import BitableClient

            call_count = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                call_count[0] += 1
                if call_count[0] == 1:
                    return today_records
                return []

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 1
        assert "10" in result.output
        assert "WARMUP" in result.output or "warmup" in result.output.lower()

    def test_warmup_mode_banner_shown(self):
        """WARMUP_MODE shows banner but proceeds if under limit."""
        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = _make_settings(warmup_mode=True, warmup_daily_limit=10)

            from src.bitable_client import BitableClient

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=[]):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 0
        assert "WARMUP" in result.output or "warmup" in result.output.lower()

    def test_sends_with_delay_between_messages(self):
        """Verify time.sleep is called between sends."""
        import json

        contacts = []
        for i, name in enumerate(["A", "B"]):
            contacts.append({
                "record_id": f"r_{name}",
                "fields": {
                    "姓名": name,
                    "职位": "CFO",
                    "公司名称": "Co",
                    "LinkedIn URL": f"https://linkedin.com/in/{name.lower()}/",
                    "联系人类型": "CFO",
                    "Flow类型": "re_activation",
                    "DM状态": "day1_queued",
                    "Provider ID": f"pid_{name}",
                    "Chat ID": f"chat_{name}",
                    "触达次数": 0,
                    "Lead评分": 50,
                    "DM草稿": json.dumps([{"touch": "day1", "draft": f"Hi {name}!", "generated_at": "2024-01-01", "sent_at": None}]),
                },
            })

        sleep_calls: list[float] = []

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.cli.time") as mock_time:
            mock_settings.return_value = _make_settings()

            def track_sleep(secs):
                sleep_calls.append(secs)

            mock_time.sleep = track_sleep

            from src.bitable_client import BitableClient
            from src.unipile_client import UnipileClient

            list_call = [0]

            def mock_list(table_id, filter_formula=None, page_size=100):
                list_call[0] += 1
                if list_call[0] == 1:
                    return []  # daily count
                if list_call[0] == 2:
                    return contacts
                return []

            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", side_effect=mock_list), \
                 patch.object(BitableClient, "update_record", return_value={}), \
                 patch.object(UnipileClient, "send_message", return_value={"id": "m"}):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "push-dms", "--touch", "day1", "--confirm-all",
                ])

        assert result.exit_code == 0
        assert "2 sent" in result.output
        # Should have slept at least once between first and second send (20-40s range)
        assert len(sleep_calls) >= 1
        assert any(20 <= s <= 40 for s in sleep_calls)
