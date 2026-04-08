"""Data enrichment logic (stub)."""


class EnrichmentService:
    """Enrich company and contact data from external signals."""

    def enrich_company(self, company_name: str, website: str | None = None) -> dict:
        raise NotImplementedError

    def enrich_contact(self, linkedin_url: str) -> dict:
        raise NotImplementedError
