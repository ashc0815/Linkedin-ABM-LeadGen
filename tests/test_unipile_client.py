"""Tests for UnipileClient."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.unipile_client import UnipileClient, extract_username


# ---------------------------------------------------------------------------
# extract_username
# ---------------------------------------------------------------------------


class TestExtractUsername:
    def test_standard_url(self):
        assert extract_username("https://www.linkedin.com/in/johndoe/") == "johndoe"

    def test_url_without_trailing_slash(self):
        assert extract_username("https://www.linkedin.com/in/johndoe") == "johndoe"

    def test_url_with_query_params(self):
        assert extract_username("https://linkedin.com/in/jane-smith?trk=abc") == "jane-smith"

    def test_company_url_returns_none(self):
        assert extract_username("https://linkedin.com/company/acme/") is None

    def test_empty_string(self):
        assert extract_username("") is None

    def test_url_with_locale(self):
        assert extract_username("https://au.linkedin.com/in/bob123") == "bob123"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    c = UnipileClient(api_key="test_key", dsn="https://api.unipile.test", account_id="acc_123")
    c._rate_limiter.wait = lambda: None  # disable rate limiter in tests
    return c


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# resolve_provider_id
# ---------------------------------------------------------------------------


class TestResolveProviderId:
    def test_successful_resolve(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({"provider_id": "pid_abc"})):
            result = client.resolve_provider_id("https://linkedin.com/in/johndoe/")
        assert result == "pid_abc"

    def test_uses_id_fallback(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({"id": "id_xyz"})):
            result = client.resolve_provider_id("https://linkedin.com/in/johndoe/")
        assert result == "id_xyz"

    def test_returns_none_for_404(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=404)):
            result = client.resolve_provider_id("https://linkedin.com/in/unknown/")
        assert result is None

    def test_returns_none_for_403_locked(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=403)):
            result = client.resolve_provider_id("https://linkedin.com/in/locked/")
        assert result is None

    def test_returns_none_for_invalid_url(self, client):
        result = client.resolve_provider_id("https://linkedin.com/company/acme/")
        assert result is None

    def test_passes_correct_params(self, client):
        mock_get = MagicMock(return_value=_mock_response({"provider_id": "pid"}))
        with patch.object(client._http, "get", mock_get):
            client.resolve_provider_id("https://linkedin.com/in/testuser/")
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/api/v1/users/testuser" in call_args[0][0]
        assert call_args[1]["params"]["account_id"] == "acc_123"


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_returns_profile_data(self, client):
        profile_data = {
            "provider_id": "pid_abc",
            "headline": "CFO at Acme",
            "about": "Experienced finance leader",
            "experience": [{"company": "Acme", "title": "CFO"}],
        }
        with patch.object(client._http, "get", return_value=_mock_response(profile_data)):
            result = client.get_profile("pid_abc")
        assert result["headline"] == "CFO at Acme"
        assert result["about"] == "Experienced finance leader"

    def test_passes_linkedin_sections_star(self, client):
        mock_get = MagicMock(return_value=_mock_response({}))
        with patch.object(client._http, "get", mock_get):
            client.get_profile("pid_abc")
        params = mock_get.call_args[1]["params"]
        assert params["linkedin_sections"] == "*"


# ---------------------------------------------------------------------------
# check_is_connection
# ---------------------------------------------------------------------------


class TestCheckIsConnection:
    def test_first_degree_by_number(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({"connection_level": 1})):
            assert client.check_is_connection("pid_abc") is True

    def test_first_degree_by_string(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({"distance": "FIRST"})):
            assert client.check_is_connection("pid_abc") is True

    def test_second_degree(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({"connection_level": 2})):
            assert client.check_is_connection("pid_abc") is False

    def test_no_connection_data(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({})):
            assert client.check_is_connection("pid_abc") is False

    def test_http_error_returns_false(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=500)):
            assert client.check_is_connection("pid_abc") is False


# ---------------------------------------------------------------------------
# get_existing_chat
# ---------------------------------------------------------------------------


class TestGetExistingChat:
    def test_chat_found(self, client):
        data = {"items": [{"id": "chat_xyz", "attendees": ["pid_abc"]}]}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            result = client.get_existing_chat("pid_abc")
        assert result == "chat_xyz"

    def test_chat_found_via_chat_id_key(self, client):
        data = {"chats": [{"chat_id": "chat_123"}]}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            result = client.get_existing_chat("pid_abc")
        assert result == "chat_123"

    def test_no_chat(self, client):
        data = {"items": []}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            result = client.get_existing_chat("pid_abc")
        assert result is None

    def test_http_error_returns_none(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=500)):
            result = client.get_existing_chat("pid_abc")
        assert result is None

    def test_passes_attendees_ids_param(self, client):
        mock_get = MagicMock(return_value=_mock_response({"items": []}))
        with patch.object(client._http, "get", mock_get):
            client.get_existing_chat("pid_abc")
        params = mock_get.call_args[1]["params"]
        assert params["attendees_ids"] == "pid_abc"
        assert params["account_id"] == "acc_123"


# ---------------------------------------------------------------------------
# view_profile
# ---------------------------------------------------------------------------


class TestViewProfile:
    def test_success(self, client):
        with patch.object(client._http, "post", return_value=_mock_response({})):
            assert client.view_profile("pid_abc") is True

    def test_http_error_returns_false(self, client):
        with patch.object(client._http, "post", return_value=_mock_response({}, status_code=500)):
            assert client.view_profile("pid_abc") is False

    def test_posts_to_correct_url(self, client):
        mock_post = MagicMock(return_value=_mock_response({}))
        with patch.object(client._http, "post", mock_post):
            client.view_profile("pid_abc")
        url = mock_post.call_args[0][0]
        assert "/api/v1/users/pid_abc/view" in url


# ---------------------------------------------------------------------------
# get_recent_posts
# ---------------------------------------------------------------------------


class TestGetRecentPosts:
    def test_returns_posts(self, client):
        data = {"items": [{"id": "post_1", "text": "Hello"}, {"id": "post_2", "text": "World"}]}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            posts = client.get_recent_posts("pid_abc", limit=2)
        assert len(posts) == 2
        assert posts[0]["id"] == "post_1"

    def test_empty_on_error(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=404)):
            assert client.get_recent_posts("pid_abc") == []

    def test_uses_posts_key(self, client):
        data = {"posts": [{"id": "p1"}]}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            assert len(client.get_recent_posts("pid_abc")) == 1


# ---------------------------------------------------------------------------
# react_to_post
# ---------------------------------------------------------------------------


class TestReactToPost:
    def test_success(self, client):
        with patch.object(client._http, "post", return_value=_mock_response({})):
            assert client.react_to_post("post_1") is True

    def test_failure(self, client):
        with patch.object(client._http, "post", return_value=_mock_response({}, status_code=400)):
            assert client.react_to_post("post_1") is False

    def test_sends_correct_body(self, client):
        mock_post = MagicMock(return_value=_mock_response({}))
        with patch.object(client._http, "post", mock_post):
            client.react_to_post("post_1", reaction="CELEBRATE")
        body = mock_post.call_args[1]["json"]
        assert body["reaction_type"] == "CELEBRATE"
        assert body["account_id"] == "acc_123"


# ---------------------------------------------------------------------------
# send_connection_request
# ---------------------------------------------------------------------------


class TestSendConnectionRequest:
    def test_returns_chat_data(self, client):
        resp_data = {"id": "chat_new", "status": "pending"}
        with patch.object(client._http, "post", return_value=_mock_response(resp_data)):
            result = client.send_connection_request("pid_abc", note="Hi there")
        assert result["id"] == "chat_new"

    def test_sends_correct_body(self, client):
        mock_post = MagicMock(return_value=_mock_response({"id": "c1"}))
        with patch.object(client._http, "post", mock_post):
            client.send_connection_request("pid_abc", note="Hello")
        body = mock_post.call_args[1]["json"]
        assert body["attendees_ids"] == ["pid_abc"]
        assert body["text"] == "Hello"
        assert body["account_id"] == "acc_123"

    def test_no_note(self, client):
        mock_post = MagicMock(return_value=_mock_response({"id": "c1"}))
        with patch.object(client._http, "post", mock_post):
            client.send_connection_request("pid_abc")
        body = mock_post.call_args[1]["json"]
        assert "text" not in body


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_sends_to_chat(self, client):
        with patch.object(client._http, "post", return_value=_mock_response({"id": "msg_1"})):
            result = client.send_message("chat_xyz", "Hello there")
        assert result["id"] == "msg_1"

    def test_sends_correct_url_and_body(self, client):
        mock_post = MagicMock(return_value=_mock_response({}))
        with patch.object(client._http, "post", mock_post):
            client.send_message("chat_xyz", "Test message")
        url = mock_post.call_args[0][0]
        assert "/api/v1/chats/chat_xyz/messages" in url
        assert mock_post.call_args[1]["json"]["text"] == "Test message"


# ---------------------------------------------------------------------------
# check_pending_invitations
# ---------------------------------------------------------------------------


class TestCheckPendingInvitations:
    def test_returns_items(self, client):
        data = {"items": [{"id": "inv_1"}, {"id": "inv_2"}]}
        with patch.object(client._http, "get", return_value=_mock_response(data)):
            result = client.check_pending_invitations()
        assert len(result) == 2

    def test_empty_on_error(self, client):
        with patch.object(client._http, "get", return_value=_mock_response({}, status_code=500)):
            assert client.check_pending_invitations() == []


# ---------------------------------------------------------------------------
# withdraw_invitation
# ---------------------------------------------------------------------------


class TestWithdrawInvitation:
    def test_success(self, client):
        mock_delete = MagicMock()
        mock_delete.raise_for_status = MagicMock()
        mock_delete.json.return_value = {}
        with patch.object(client._http, "delete", return_value=mock_delete):
            assert client.withdraw_invitation("inv_1") is True

    def test_failure(self, client):
        mock_resp = _mock_response({}, status_code=404)
        with patch.object(client._http, "delete", return_value=mock_resp):
            assert client.withdraw_invitation("inv_1") is False
