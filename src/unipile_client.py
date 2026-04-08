"""Unipile API client for LinkedIn profile resolution, engagement, and messaging."""

from __future__ import annotations

import logging
import re

import httpx

from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Regex to extract username from LinkedIn profile URL: /in/username/ → username
_USERNAME_RE = re.compile(r"/in/([^/?#]+)")


class UnipileRateLimitError(Exception):
    """Raised when Unipile returns HTTP 429 (too many requests)."""


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

    def _check_rate_limit(self, resp: httpx.Response) -> None:
        """Raise UnipileRateLimitError on HTTP 429."""
        if resp.status_code == 429:
            raise UnipileRateLimitError(
                f"Unipile rate limit hit (429). Retry-After: "
                f"{resp.headers.get('Retry-After', 'unknown')}"
            )

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Rate-limited GET request to the Unipile API."""
        self._rate_limiter.wait()
        url = f"{self.dsn}{path}"
        resp = self._http.get(url, headers=self._headers(), params=params)
        self._check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: dict | None = None) -> dict:
        """Rate-limited POST request to the Unipile API."""
        self._rate_limiter.wait()
        url = f"{self.dsn}{path}"
        resp = self._http.post(url, headers=self._headers(), json=json_body)
        self._check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> bool:
        """Rate-limited DELETE request. Returns True on success."""
        self._rate_limiter.wait()
        url = f"{self.dsn}{path}"
        resp = self._http.delete(url, headers=self._headers())
        self._check_rate_limit(resp)
        resp.raise_for_status()
        return True

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
        """Fetch a full LinkedIn profile with all sections."""
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

        distance = data.get("connection_level") or data.get("distance") or 0
        is_first = distance == 1 or str(distance) == "FIRST"
        if is_first:
            logger.info("provider_id=%s is a 1st-degree connection", provider_id)
        return is_first

    # ------------------------------------------------------------------
    # Chat lookup
    # ------------------------------------------------------------------

    def get_existing_chat(self, provider_id: str) -> str | None:
        """Search for an existing LinkedIn chat with the given provider_id."""
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
    # Engagement warming
    # ------------------------------------------------------------------

    def view_profile(self, provider_id: str) -> bool:
        """Visit a LinkedIn profile so the user sees Tony in notifications."""
        try:
            self._post(
                f"/api/v1/users/{provider_id}/view",
                json_body={"account_id": self.account_id},
            )
            logger.info("Viewed profile provider_id=%s", provider_id)
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning("view_profile failed for %s: %d", provider_id, exc.response.status_code)
            return False

    def get_recent_posts(self, provider_id: str, limit: int = 3) -> list[dict]:
        """Fetch recent posts by the given user."""
        try:
            data = self._get(
                f"/api/v1/users/{provider_id}/posts",
                params={"account_id": self.account_id, "limit": limit},
            )
        except httpx.HTTPStatusError:
            return []

        items = data.get("items") or data.get("posts") or []
        logger.debug("get_recent_posts(%s): %d posts", provider_id, len(items))
        return items

    def react_to_post(self, post_id: str, reaction: str = "LIKE") -> bool:
        """React (like) a LinkedIn post."""
        try:
            self._post(
                f"/api/v1/posts/{post_id}/reactions",
                json_body={
                    "account_id": self.account_id,
                    "reaction_type": reaction,
                },
            )
            logger.info("Reacted %s to post %s", reaction, post_id)
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning("react_to_post failed for %s: %d", post_id, exc.response.status_code)
            return False

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_connection_request(self, provider_id: str, note: str | None = None) -> dict:
        """Send a LinkedIn connection request (cold_new Day 1).

        Returns the chat/invitation dict from the API.
        """
        body: dict = {
            "account_id": self.account_id,
            "attendees_ids": [provider_id],
        }
        if note:
            body["text"] = note
        data = self._post("/api/v1/chats", json_body=body)
        chat_id = data.get("id") or data.get("chat_id")
        logger.info("Sent connection request to %s, chat_id=%s", provider_id, chat_id)
        return data

    def send_message(self, chat_id: str, text: str) -> dict:
        """Send a DM into an existing chat (re_activation + follow-ups)."""
        data = self._post(
            f"/api/v1/chats/{chat_id}/messages",
            json_body={"text": text},
        )
        logger.info("Sent message to chat %s (%d chars)", chat_id, len(text))
        return data

    def get_messages(self, chat_id: str) -> list[dict]:
        """List messages in a chat."""
        data = self._get(
            f"/api/v1/chats/{chat_id}/messages",
            params={"account_id": self.account_id},
        )
        return data.get("items") or data.get("messages") or []

    # ------------------------------------------------------------------
    # Invitations
    # ------------------------------------------------------------------

    def check_pending_invitations(self) -> list[dict]:
        """List pending outgoing connection requests."""
        try:
            data = self._get(
                "/api/v1/invitations",
                params={"account_id": self.account_id, "status": "pending"},
            )
        except httpx.HTTPStatusError:
            return []
        return data.get("items") or data.get("invitations") or []

    def find_invitation_for(self, provider_id: str) -> str | None:
        """Find the invitation_id for a pending connection request to provider_id."""
        invitations = self.check_pending_invitations()
        for inv in invitations:
            invitee = (
                inv.get("provider_id")
                or inv.get("attendee_id")
                or inv.get("invitee", {}).get("provider_id")
                or ""
            )
            if invitee == provider_id:
                return inv.get("id") or inv.get("invitation_id")
        return None

    def withdraw_invitation(self, invitation_id: str) -> bool:
        """Withdraw a pending connection request."""
        try:
            return self._delete(f"/api/v1/invitations/{invitation_id}")
        except httpx.HTTPStatusError as exc:
            logger.warning("withdraw_invitation failed for %s: %d", invitation_id, exc.response.status_code)
            return False
