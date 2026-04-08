"""Unipile API client for LinkedIn messaging (stub)."""


class UnipileClient:
    """Client for sending/receiving LinkedIn messages via Unipile."""

    def __init__(self, api_key: str, dsn: str, account_id: str) -> None:
        self.api_key = api_key
        self.dsn = dsn
        self.account_id = account_id

    def send_message(self, provider_id: str, text: str) -> dict:
        raise NotImplementedError

    def get_messages(self, chat_id: str) -> list[dict]:
        raise NotImplementedError

    def get_profile(self, provider_id: str) -> dict:
        raise NotImplementedError

    def list_chats(self) -> list[dict]:
        raise NotImplementedError
