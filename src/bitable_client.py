"""Feishu Bitable CRUD client (stub)."""


class BitableClient:
    """Client for reading/writing records in Feishu Bitable tables."""

    def __init__(self, app_id: str, app_secret: str, app_token: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token

    def list_records(self, table_id: str) -> list[dict]:
        raise NotImplementedError

    def create_record(self, table_id: str, fields: dict) -> dict:
        raise NotImplementedError

    def update_record(self, table_id: str, record_id: str, fields: dict) -> dict:
        raise NotImplementedError

    def batch_upsert(self, table_id: str, records: list[dict]) -> list[dict]:
        raise NotImplementedError
