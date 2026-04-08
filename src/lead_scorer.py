"""Lead scoring logic (stub)."""


class LeadScorer:
    """Score companies and contacts based on enrichment signals."""

    def score_company(self, company: dict) -> int:
        raise NotImplementedError

    def score_contact(self, contact: dict, company_score: int) -> int:
        raise NotImplementedError
