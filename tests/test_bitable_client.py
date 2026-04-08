"""Tests for BitableClient using mocked HTTP responses."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.bitable_client import (
    COMPANY_FIELD_MAP,
    CONTACT_FIELD_MAP,
    BitableAPIError,
    BitableClient,
)
from src.models import Company, Contact


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Create a BitableClient with rate limiting effectively disabled."""
    c = BitableClient(
        app_id="test_app_id",
        app_secret="test_app_secret",
        app_token="test_app_token",
        companies_table_id="tbl_companies",
        contacts_table_id="tbl_contacts",
    )
    # Pre-set a valid token so tests don't hit the auth endpoint
    c._token = "fake_token"
    c._token_expires_at = time.monotonic() + 9999
    # Disable rate limiter waits in tests
    c._rate_limiter.wait = lambda: None
    return c


def _ok_response(data: dict | None = None) -> dict:
    return {"code": 0, "msg": "success", "data": data or {}}


def _make_record(record_id: str, fields: dict) -> dict:
    return {"record_id": record_id, "fields": fields}


SAMPLE_COMPANY_FIELDS = {
    "公司名称": "Acme Corp",
    "LinkedIn URL": "https://linkedin.com/company/acme",
    "行业": "Manufacturing",
    "员工数": 500,
    "触达状态": "未触达",
    "来源": "Apify",
    "Lead评分": 0,
    "备注": "",
}

SAMPLE_CONTACT_FIELDS = {
    "姓名": "John Doe",
    "职位": "CFO",
    "公司名称": "Acme Corp",
    "LinkedIn URL": "https://linkedin.com/in/johndoe",
    "联系人类型": "CFO",
    "Flow类型": "cold_new",
    "DM状态": "not_started",
    "触达次数": 0,
    "Lead评分": 0,
}


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    def test_refresh_token_success(self):
        client = BitableClient("aid", "asecret", "atok")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "tenant_access_token": "new_token_123",
            "expire": 7200,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._http, "post", return_value=mock_resp):
            token = client._refresh_token()

        assert token == "new_token_123"
        assert client._token == "new_token_123"

    def test_refresh_token_error_raises(self):
        client = BitableClient("aid", "asecret", "atok")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 10003, "msg": "invalid app_secret"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._http, "post", return_value=mock_resp):
            with pytest.raises(BitableAPIError, match="10003"):
                client._refresh_token()


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_list_records_single_page(self, client):
        records = [_make_record("r1", SAMPLE_COMPANY_FIELDS)]
        resp = _ok_response({"items": records, "has_more": False, "total": 1})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            result = client.list_records("tbl_companies")

        assert len(result) == 1
        assert result[0]["record_id"] == "r1"

    def test_list_records_pagination(self, client):
        page1 = _ok_response({
            "items": [_make_record("r1", SAMPLE_COMPANY_FIELDS)],
            "has_more": True,
            "page_token": "pt2",
        })
        page2 = _ok_response({
            "items": [_make_record("r2", SAMPLE_COMPANY_FIELDS)],
            "has_more": False,
        })
        call_count = 0

        def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = page1 if call_count == 1 else page2
            return resp

        with patch.object(client._http, "request", side_effect=mock_request):
            result = client.list_records("tbl_companies")

        assert len(result) == 2
        assert call_count == 2

    def test_create_record(self, client):
        created = _make_record("r_new", SAMPLE_COMPANY_FIELDS)
        resp = _ok_response({"record": created})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            result = client.create_record("tbl_companies", SAMPLE_COMPANY_FIELDS)

        assert result["record_id"] == "r_new"

    def test_batch_create_records(self, client):
        records_in = [SAMPLE_COMPANY_FIELDS, SAMPLE_COMPANY_FIELDS]
        created = [_make_record("r1", SAMPLE_COMPANY_FIELDS), _make_record("r2", SAMPLE_COMPANY_FIELDS)]
        resp = _ok_response({"records": created})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            result = client.batch_create_records("tbl_companies", records_in)

        assert len(result) == 2

    def test_update_record(self, client):
        updated = _make_record("r1", {**SAMPLE_COMPANY_FIELDS, "备注": "updated"})
        resp = _ok_response({"record": updated})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            result = client.update_record("tbl_companies", "r1", {"备注": "updated"})

        assert result["record_id"] == "r1"

    def test_search_records(self, client):
        records = [_make_record("r1", SAMPLE_COMPANY_FIELDS)]
        resp = _ok_response({"items": records, "has_more": False})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            result = client.search_records("tbl_companies", 'CurrentValue.[公司名称] = "Acme Corp"')

        assert len(result) == 1


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_company_round_trip(self):
        company = Company(
            company_name="Acme Corp",
            linkedin_url="https://linkedin.com/company/acme",
            industry="Manufacturing",
            employee_count=500,
            enrichment_signals={"sap_mentions": 3},
        )
        fields = BitableClient._company_to_fields(company)
        assert fields["公司名称"] == "Acme Corp"
        assert fields["行业"] == "Manufacturing"
        # enrichment_signals should be JSON-encoded
        assert json.loads(fields["增强信号"]) == {"sap_mentions": 3}

        # Round-trip back
        restored = BitableClient._fields_to_company(fields)
        assert restored.company_name == company.company_name
        assert restored.enrichment_signals == {"sap_mentions": 3}

    def test_contact_round_trip(self):
        contact = Contact(
            name="Jane Smith",
            title="Finance Director",
            company_name="Acme Corp",
            linkedin_url="https://linkedin.com/in/janesmith",
            contact_type="Finance Director",
            flow_type="cold_new",
            dm_drafts=[{"touch": "day1", "draft": "Hi Jane"}],
        )
        fields = BitableClient._contact_to_fields(contact)
        assert fields["姓名"] == "Jane Smith"
        # dm_drafts should be JSON-encoded
        assert json.loads(fields["DM草稿"]) == [{"touch": "day1", "draft": "Hi Jane"}]

        restored = BitableClient._fields_to_contact(fields)
        assert restored.name == "Jane Smith"
        assert restored.dm_drafts == [{"touch": "day1", "draft": "Hi Jane"}]


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------

class TestConvenience:
    def test_find_company_by_linkedin_url_found(self, client):
        record = _make_record("r1", SAMPLE_COMPANY_FIELDS)
        resp = _ok_response({"items": [record], "has_more": False})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            company = client.find_company_by_linkedin_url("https://linkedin.com/company/acme")

        assert company is not None
        assert company.company_name == "Acme Corp"

    def test_find_company_by_linkedin_url_not_found(self, client):
        resp = _ok_response({"items": [], "has_more": False})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            company = client.find_company_by_linkedin_url("https://linkedin.com/company/missing")

        assert company is None

    def test_find_contact_by_linkedin_url(self, client):
        record = _make_record("r1", SAMPLE_CONTACT_FIELDS)
        resp = _ok_response({"items": [record], "has_more": False})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            contact = client.find_contact_by_linkedin_url("https://linkedin.com/in/johndoe")

        assert contact is not None
        assert contact.name == "John Doe"

    def test_upsert_company_creates_when_new(self, client):
        company = Company(
            company_name="NewCorp",
            linkedin_url="https://linkedin.com/company/newcorp",
            industry="Other",
        )
        # First call: search returns empty → second call: create
        empty_resp = _ok_response({"items": [], "has_more": False})
        created_resp = _ok_response({"record": _make_record("r_new", {})})
        responses = iter([empty_resp, created_resp])

        def mock_request(*args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = next(responses)
            return resp

        with patch.object(client._http, "request", side_effect=mock_request):
            result = client.upsert_company(company)

        assert result["record_id"] == "r_new"

    def test_upsert_company_updates_when_exists(self, client):
        company = Company(
            company_name="Acme Corp",
            linkedin_url="https://linkedin.com/company/acme",
            industry="Manufacturing",
        )
        existing = _make_record("r_existing", SAMPLE_COMPANY_FIELDS)
        search_resp = _ok_response({"items": [existing], "has_more": False})
        update_resp = _ok_response({"record": existing})
        responses = iter([search_resp, update_resp])

        def mock_request(*args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = next(responses)
            return resp

        with patch.object(client._http, "request", side_effect=mock_request):
            result = client.upsert_company(company)

        assert result["record_id"] == "r_existing"

    def test_get_contacts_by_status(self, client):
        record = _make_record("r1", SAMPLE_CONTACT_FIELDS)
        resp = _ok_response({"items": [record], "has_more": False})

        with patch.object(client._http, "request", return_value=MagicMock(
            json=MagicMock(return_value=resp),
            raise_for_status=MagicMock(),
        )):
            contacts = client.get_contacts_by_status("not_started", flow_type="cold_new")

        assert len(contacts) == 1
        assert contacts[0].name == "John Doe"


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

class TestRetry:
    def test_retries_on_transport_error(self, client):
        import httpx as _httpx

        call_count = 0

        def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _httpx.TransportError("connection reset")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _ok_response({"items": [], "has_more": False})
            return resp

        with patch.object(client._http, "request", side_effect=mock_request), \
             patch("src.bitable_client.time.sleep"):
            result = client.list_records("tbl_companies")

        assert result == []
        assert call_count == 3

    def test_raises_after_max_retries(self, client):
        import httpx as _httpx

        def mock_request(*args, **kwargs):
            raise _httpx.TransportError("connection refused")

        with patch.object(client._http, "request", side_effect=mock_request), \
             patch("src.bitable_client.time.sleep"):
            with pytest.raises(_httpx.TransportError):
                client.list_records("tbl_companies")


# ---------------------------------------------------------------------------
# Field mapping completeness
# ---------------------------------------------------------------------------

class TestFieldMapping:
    def test_company_map_covers_all_model_fields(self):
        model_fields = set(Company.model_fields.keys())
        mapped_fields = set(COMPANY_FIELD_MAP.keys())
        assert model_fields == mapped_fields, f"Missing: {model_fields - mapped_fields}"

    def test_contact_map_covers_all_model_fields(self):
        model_fields = set(Contact.model_fields.keys())
        mapped_fields = set(CONTACT_FIELD_MAP.keys())
        assert model_fields == mapped_fields, f"Missing: {model_fields - mapped_fields}"
