"""DM (Direct Message) generation via Claude (stub)."""


class DMGenerator:
    """Generate personalized LinkedIn DMs using Claude API."""

    def __init__(self, anthropic_api_key: str) -> None:
        self.api_key = anthropic_api_key

    def generate_first_touch(self, contact: dict, company: dict) -> str:
        raise NotImplementedError

    def generate_follow_up(self, contact: dict, company: dict, touch_number: int) -> str:
        raise NotImplementedError
