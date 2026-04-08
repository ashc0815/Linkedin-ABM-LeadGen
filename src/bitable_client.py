"""Feishu Bitable REST API client with token caching, retry, and rate limiting."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime

import httpx

from src.models import Company, Contact
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field mapping: Python model attribute → Bitable column display name
# Adjust values if your Bitable table uses different column names.
# ---------------------------------------------------------------------------

COMPANY_FIELD_MAP: dict[str, str] = {
    "company_name": "公司名称",
    "linkedin_url": "LinkedIn URL",
    "website": "官网",
    "industry": "行业",
    "employee_count": "员工数",
    "hq_city": "总部城市",
    "has_overseas_offices": "海外办公室",
    "sap_user": "SAP用户",
    "uses_concur": "使用Concur",
    "enrichment_signals": "增强信号",
    "outreach_status": "触达状态",
    "source": "来源",
    "lead_score": "Lead评分",
    "notes": "备注",
}

CONTACT_FIELD_MAP: dict[str, str] = {
    "name": "姓名",
    "title": "职位",
    "company_name": "公司名称",
    "linkedin_url": "LinkedIn URL",
    "linkedin_provider_id": "Provider ID",
    "profile_summary": "简介",
    "contact_type": "联系人类型",
    "flow_type": "Flow类型",
    "dm_status": "DM状态",
    "last_touch_date": "上次触达",
    "next_touch_date": "下次触达",
    "touch_count": "触达次数",
    "reply_summary": "回复摘要",
    "dm_drafts": "DM草稿",
    "is_existing_connection": "已有连接",
    "unipile_chat_id": "Chat ID",
    "lead_score": "Lead评分",
}

# Reverse maps for reading records back into models
_COMPANY_REVERSE_MAP = {v: k for k, v in COMPANY_FIELD_MAP.items()}
_CONTACT_REVERSE_MAP = {v: k for k, v in CONTACT_FIELD_MAP.items()}

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
BITABLE_BASE = "https://open.feishu.cn/open-apis/bitable/v1/apps"

MAX_RETRIES = 3
BATCH_LIMIT = 500


class BitableAPIError(Exception):
    """Raised when a Bitable API call fails after retries."""

    def __init__(self, code: int, msg: str) -> None:
        self.code = code
        super().__init__(f"Bitable API error {code}: {msg}")


class BitableClient:
    """Synchronous client for Feishu Bitable with auto-refreshing token."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        app_token: str,
        companies_table_id: str = "",
        contacts_table_id: str = "",
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.companies_table_id = companies_table_id
        self.contacts_table_id = contacts_table_id

        self._http = httpx.Client(timeout=30.0)
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._rate_limiter = RateLimiter(max_calls_per_minute=100)

    @classmethod
    def from_settings(cls, settings) -> "BitableClient":
        """Create a BitableClient from a Settings object."""
        return cls(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            app_token=settings.feishu_bitable_app_token,
            companies_table_id=settings.feishu_companies_table_id,
            contacts_table_id=settings.feishu_contacts_table_id,
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """Return a valid tenant_access_token, refreshing if needed."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        return self._refresh_token()

    def get_tenant_token(self) -> str:
        """Public accessor: return a valid token (refreshing if needed)."""
        return self._ensure_token()

    def _refresh_token(self) -> str:
        logger.debug("Refreshing tenant_access_token")
        resp = self._http.post(
            AUTH_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise BitableAPIError(data.get("code", -1), data.get("msg", "token request failed"))
        self._token = data["tenant_access_token"]
        # Feishu tokens last 2 hours; refresh 5 min early
        expire_secs = data.get("expire", 7200)
        self._token_expires_at = time.monotonic() + expire_secs - 300
        logger.info("tenant_access_token refreshed, expires in %ds", expire_secs)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_token()}"}

    # ------------------------------------------------------------------
    # Low-level HTTP with retry + rate limiting
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Execute an HTTP request with up to MAX_RETRIES retries and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limiter.wait()
            try:
                resp = self._http.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                # Handle token expiry mid-flight (code 99991663 / 99991661)
                if data.get("code") in (99991663, 99991661):
                    logger.warning("Token expired mid-request, refreshing")
                    self._refresh_token()
                    continue

                if data.get("code") != 0:
                    raise BitableAPIError(data.get("code", -1), data.get("msg", "unknown"))
                return data
            except (httpx.HTTPStatusError, httpx.TransportError, BitableAPIError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait = 2**attempt
                    logger.warning("Request failed (attempt %d/%d): %s — retrying in %ds",
                                   attempt, MAX_RETRIES, exc, wait)
                    time.sleep(wait)
                    # Refresh token on auth errors
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
                        self._refresh_token()

        raise last_exc  # type: ignore[misc]

    def _records_url(self, table_id: str) -> str:
        return f"{BITABLE_BASE}/{self.app_token}/tables/{table_id}/records"

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def list_records(
        self,
        table_id: str,
        filter_formula: str | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Paginate through all records, returning a list of raw record dicts."""
        all_records: list[dict] = []
        page_token: str | None = None
        url = self._records_url(table_id)

        while True:
            params: dict[str, str | int] = {"page_size": min(page_size, 500)}
            if filter_formula:
                params["filter"] = filter_formula
            if page_token:
                params["page_token"] = page_token

            data = self._request("GET", url, params=params)
            items = data.get("data", {}).get("items", [])
            all_records.extend(items)

            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data["data"].get("page_token")

        logger.info("list_records(%s): fetched %d records", table_id, len(all_records))
        return all_records

    def create_record(self, table_id: str, fields: dict) -> dict:
        """Create a single record and return the created record dict."""
        url = self._records_url(table_id)
        data = self._request("POST", url, json_body={"fields": fields})
        record = data.get("data", {}).get("record", {})
        logger.info("Created record %s in table %s", record.get("record_id"), table_id)
        return record

    def batch_create_records(self, table_id: str, records: list[dict]) -> list[dict]:
        """Batch create records (max 500 per batch). Returns all created records."""
        url = f"{self._records_url(table_id)}/batch_create"
        created: list[dict] = []
        for i in range(0, len(records), BATCH_LIMIT):
            batch = records[i : i + BATCH_LIMIT]
            payload = {"records": [{"fields": r} for r in batch]}
            data = self._request("POST", url, json_body=payload)
            created.extend(data.get("data", {}).get("records", []))
        logger.info("batch_create_records(%s): created %d records", table_id, len(created))
        return created

    def update_record(self, table_id: str, record_id: str, fields: dict) -> dict:
        """Update a single record by record_id."""
        url = f"{self._records_url(table_id)}/{record_id}"
        data = self._request("PUT", url, json_body={"fields": fields})
        record = data.get("data", {}).get("record", {})
        logger.info("Updated record %s in table %s", record_id, table_id)
        return record

    def search_records(self, table_id: str, filter_formula: str) -> list[dict]:
        """Search records using a Bitable filter formula."""
        return self.list_records(table_id, filter_formula=filter_formula)

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _company_to_fields(company: Company) -> dict:
        """Convert a Company model to Bitable fields dict."""
        raw = company.model_dump()
        fields: dict = {}
        for attr, col_name in COMPANY_FIELD_MAP.items():
            val = raw.get(attr)
            if val is None:
                continue
            # JSON-encode dict fields
            if isinstance(val, dict):
                val = json.dumps(val, ensure_ascii=False)
            fields[col_name] = val
        return fields

    @staticmethod
    def _fields_to_company(fields: dict) -> Company:
        """Convert Bitable fields dict back to a Company model."""
        kwargs: dict = {}
        for col_name, val in fields.items():
            attr = _COMPANY_REVERSE_MAP.get(col_name)
            if attr is None:
                continue
            # JSON-decode string → dict for enrichment_signals
            if attr == "enrichment_signals" and isinstance(val, str):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    val = {}
            kwargs[attr] = val
        return Company(**kwargs)

    @staticmethod
    def _contact_to_fields(contact: Contact) -> dict:
        """Convert a Contact model to Bitable fields dict."""
        raw = contact.model_dump()
        fields: dict = {}
        for attr, col_name in CONTACT_FIELD_MAP.items():
            val = raw.get(attr)
            if val is None:
                continue
            if isinstance(val, dict):
                val = json.dumps(val, ensure_ascii=False)
            elif isinstance(val, list):
                val = json.dumps(val, ensure_ascii=False)
            elif isinstance(val, datetime):
                val = int(val.timestamp() * 1000)  # Bitable expects ms timestamp
            fields[col_name] = val
        return fields

    @staticmethod
    def _fields_to_contact(fields: dict) -> Contact:
        """Convert Bitable fields dict back to a Contact model."""
        kwargs: dict = {}
        for col_name, val in fields.items():
            attr = _CONTACT_REVERSE_MAP.get(col_name)
            if attr is None:
                continue
            # JSON-decode list/dict fields
            if attr == "dm_drafts" and isinstance(val, str):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    val = []
            # Bitable returns ms timestamps for date fields
            if attr in ("last_touch_date", "next_touch_date") and isinstance(val, (int, float)):
                val = datetime.fromtimestamp(val / 1000)
            kwargs[attr] = val
        return Contact(**kwargs)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    @staticmethod
    def _eq_filter(field_map: dict[str, str], attr: str, value: str) -> str:
        col = field_map[attr]
        return f'CurrentValue.[{col}] = "{value}"'

    def find_company_by_linkedin_url(self, url: str) -> Company | None:
        """Find a single company by its LinkedIn URL."""
        formula = self._eq_filter(COMPANY_FIELD_MAP, "linkedin_url", url)
        records = self.list_records(self.companies_table_id, filter_formula=formula, page_size=1)
        if not records:
            return None
        return self._fields_to_company(records[0].get("fields", {}))

    def find_contact_by_linkedin_url(self, url: str) -> Contact | None:
        """Find a single contact by its LinkedIn URL."""
        formula = self._eq_filter(CONTACT_FIELD_MAP, "linkedin_url", url)
        records = self.list_records(self.contacts_table_id, filter_formula=formula, page_size=1)
        if not records:
            return None
        return self._fields_to_contact(records[0].get("fields", {}))

    def get_contacts_by_status(
        self, dm_status: str, flow_type: str | None = None
    ) -> list[Contact]:
        """Get contacts filtered by dm_status and optionally flow_type."""
        status_filter = self._eq_filter(CONTACT_FIELD_MAP, "dm_status", dm_status)
        if flow_type:
            flow_filter = self._eq_filter(CONTACT_FIELD_MAP, "flow_type", flow_type)
            formula = f"AND({status_filter}, {flow_filter})"
        else:
            formula = status_filter
        records = self.list_records(self.contacts_table_id, filter_formula=formula)
        return [self._fields_to_contact(r.get("fields", {})) for r in records]

    def get_contacts_due_today(self) -> list[Contact]:
        """Get contacts whose next_touch_date is today or earlier."""
        today_str = date.today().isoformat()
        col = CONTACT_FIELD_MAP["next_touch_date"]
        formula = f'CurrentValue.[{col}] <= "{today_str}"'
        records = self.list_records(self.contacts_table_id, filter_formula=formula)
        return [self._fields_to_contact(r.get("fields", {})) for r in records]

    def upsert_company(self, company: Company) -> dict:
        """Create or update a company by linkedin_url."""
        formula = self._eq_filter(COMPANY_FIELD_MAP, "linkedin_url", company.linkedin_url)
        existing = self.list_records(
            self.companies_table_id, filter_formula=formula, page_size=1
        )
        fields = self._company_to_fields(company)
        if existing:
            record_id = existing[0]["record_id"]
            logger.info("Upserting company %s → update %s", company.company_name, record_id)
            return self.update_record(self.companies_table_id, record_id, fields)
        logger.info("Upserting company %s → create", company.company_name)
        return self.create_record(self.companies_table_id, fields)

    def upsert_contact(self, contact: Contact) -> dict:
        """Create or update a contact by linkedin_url."""
        formula = self._eq_filter(CONTACT_FIELD_MAP, "linkedin_url", contact.linkedin_url)
        existing = self.list_records(
            self.contacts_table_id, filter_formula=formula, page_size=1
        )
        fields = self._contact_to_fields(contact)
        if existing:
            record_id = existing[0]["record_id"]
            logger.info("Upserting contact %s → update %s", contact.name, record_id)
            return self.update_record(self.contacts_table_id, record_id, fields)
        logger.info("Upserting contact %s → create", contact.name)
        return self.create_record(self.contacts_table_id, fields)

    # ------------------------------------------------------------------
    # Connection test (used by CLI init)
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, str | bool]:
        """Quick connectivity check: authenticate and probe each table.

        Returns {"auth": True/False, "companies": True/False, "contacts": True/False,
                 "error": "..." if any}.
        """
        result: dict[str, str | bool] = {"auth": False, "companies": False, "contacts": False}
        try:
            self._refresh_token()
            result["auth"] = True
        except Exception as exc:
            result["error"] = f"Auth failed: {exc}"
            return result

        for key, table_id in [
            ("companies", self.companies_table_id),
            ("contacts", self.contacts_table_id),
        ]:
            if not table_id:
                result[f"{key}_error"] = "table_id not configured"
                continue
            try:
                self.list_records(table_id, page_size=1)
                result[key] = True
            except Exception as exc:
                result[f"{key}_error"] = str(exc)

        return result
