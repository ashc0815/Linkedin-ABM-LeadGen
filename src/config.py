"""Environment configuration loaded via pydantic-settings."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    apify_api_token: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_bitable_app_token: str = ""
    feishu_companies_table_id: str = ""
    feishu_contacts_table_id: str = ""
    anthropic_api_key: str = ""
    unipile_api_key: str = ""
    unipile_dsn: str = ""
    unipile_account_id: str = ""
    brave_search_api_key: str = ""

    # Safety: warm-up period for new/recently-connected Unipile accounts
    warmup_mode: bool = False
    warmup_daily_limit: int = 10

    # Pipeline value estimation (AUD per meeting booked)
    meeting_pipeline_value: int = 50000

    @field_validator("*", mode="before")
    @classmethod
    def strip_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    def check_status(self) -> dict[str, bool]:
        """Return a mapping of field name -> whether it is configured (non-empty)."""
        return {name: bool(getattr(self, name)) for name in self.model_fields}


def load_settings() -> Settings:
    return Settings()
