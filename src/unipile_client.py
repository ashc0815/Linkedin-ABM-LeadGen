"""Unipile API client for LinkedIn profile resolution and messaging."""

from __future__ import annotations

import logging
import re

import httpx

from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Regex to extract username from LinkedIn profile URL: /in/username/ → username
_USERNAME_RE = re.compile(r"/in/([^/?#]+)")


def extract_username(linkedin_url: str) -> str | None:
    """Extract the username slug from a LinkedIn profile URL."""
    m = _USERNAME_RE.search(linkedin_url)
    return m.group(1).strip("/") if m else None


class UnipileClient:
    """Synchronous client for the Unipile LinkedIn API."""

    def __init__(self, api_key: str, dsn: str, account_id: str) -> None:
        self.api_key = api_key
        self.dsn = dsn.rstrip("/")
        self.account_id = account_id
        self._http = httpx.Client(timeout=20.0)
        self._rate_limiter = RateLimiter(
            max_calls_per_second=0,
            max_calls_per_minute=30,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Rate-limited GET request to the Unipile API."""
        self._rate_limiter.wait()
        url = f"{self.dsn}{path}"
        resp = self._http.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Profile resolution
    # ------------------------------------------------------------------

    def resolve_provider_id(self, linkedin_url: str) -> str | None:
        """Resolve a LinkedIn URL to a Unipile provider_id.

        Returns the provider_id string, or None if the profile is locked /
        not found.
        """
        username = extract_username(linkedin_url)
        if not username:
            logger.warning("Cannot extract username from URL: %s", linkedin_url)
            return None

        try:
            data = self._get(
                f"/api/v1/users/{username}",
                params={"account_id": self.account_id},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 403):
                logger.warning("Profile locked or not found: %s (%d)", username, exc.response.status_code)
                return None
            raise

        provider_id = data.get("provider_id") or data.get("id")
        if provider_id:
            logger.debug("Resolved %s → provider_id=%s", username, provider_id)
        else:
            logger.warning("No provider_id in response for %s", username)
        return provider_id

    # ------------------------------------------------------------------
    # Full profile
    # ------------------------------------------------------------------

    def get_profile(self, provider_id: str) -> dict:
        """Fetch a full LinkedIn profile with all sections.

        Returns the raw profile dict with keys like headline, about,
        experience, etc.
        """
        data = self._get(
            f"/api/v1/users/{provider_id}",
            params={
                "account_id": self.account_id,
                "linkedin_sections": "*",
            },
        )
        logger.debug("Fetched profile for provider_id=%s", provider_id)
        return data

    # ------------------------------------------------------------------
    # Connection check
    # ------------------------------------------------------------------

    def check_is_connection(self, provider_id: str) -> bool:
        """Check whether provider_id is a 1st-degree connection."""
        try:
            data = self._get(
                f"/api/v1/users/{provider_id}",
                params={"account_id": self.account_id},
            )
        except httpx.HTTPStatusError:
            return False

        # Unipile returns "connection_level" or "distance" depending on version
        distance = data.get("connection_level") or data.get("distance") or 0
        is_first = distance == 1 or str(distance) == "FIRST"
        if is_first:
            logger.info("provider_id=%s is a 1st-degree connection", provider_id)
        return is_first

    # ------------------------------------------------------------------
    # Chat lookup
    # ------------------------------------------------------------------

    def get_existing_chat(self, provider_id: str) -> str | None:
        """Search for an existing LinkedIn chat with the given provider_id.

        Returns the chat_id if found, else None.
        """
        try:
            data = self._get(
                "/api/v1/chats",
                params={
                    "account_id": self.account_id,
                    "attendees_ids": provider_id,
                },
            )
        except httpx.HTTPStatusError:
            return None

        items = data.get("items") or data.get("chats") or []
        if items:
            chat_id = items[0].get("id") or items[0].get("chat_id")
            logger.info("Found existing chat %s with provider_id=%s", chat_id, provider_id)
            return chat_id
        return None

    # ------------------------------------------------------------------
    # Messaging (kept for later use)
    # ------------------------------------------------------------------

    def send_message(self, provider_id: str, text: str) -> dict:
        """Send a LinkedIn DM to the given provider_id."""
        raise NotImplementedError

    def get_messages(self, chat_id: str) -> list[dict]:
        """List messages in a chat."""
        raise NotImplementedError
