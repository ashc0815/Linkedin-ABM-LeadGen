"""Apify integration for LinkedIn scraping (stub)."""


class ApifyLinkedInClient:
    """Wrapper around apify-client for LinkedIn company & people scraping."""

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    def scrape_company(self, linkedin_url: str) -> dict:
        raise NotImplementedError

    def scrape_employees(self, company_url: str, title_keywords: list[str]) -> list[dict]:
        raise NotImplementedError

    def search_people(self, query: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError
