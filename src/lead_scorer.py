"""Lead scoring: weighted signal model for companies and contacts (0-100)."""

from __future__ import annotations

from src.apify_client import completeness_score
from src.models import Company, Contact


# ---------------------------------------------------------------------------
# Company scoring
# ---------------------------------------------------------------------------

def _concur_sap_signal(company: Company) -> tuple[int, str]:
    """Core SAP/Concur cross-signal."""
    if company.uses_concur == "No" and company.sap_user == "Yes":
        return 30, "SAP user without Concur (ideal target)"
    if company.uses_concur == "Unknown" and company.sap_user == "Yes":
        return 20, "SAP user, Concur unknown"
    if company.uses_concur == "Yes":
        return 0, "Already uses Concur"
    if company.sap_user == "Unknown":
        return 5, "SAP status unknown"
    return 0, ""


def _overseas_signal(company: Company) -> tuple[int, str]:
    if company.has_overseas_offices:
        return 20, "Has overseas offices (cross-border compliance)"
    return 0, ""


def _headcount_signal(company: Company) -> tuple[int, str]:
    n = company.employee_count
    if 2000 <= n <= 5000:
        return 15, f"Large ({n} employees)"
    if 500 <= n < 2000:
        return 10, f"Mid-size ({n} employees)"
    if 200 <= n < 500:
        return 5, f"Small-mid ({n} employees)"
    return 0, ""


def _industry_signal(company: Company) -> tuple[int, str]:
    ind = company.industry
    if ind in ("Manufacturing", "Mining"):
        return 10, f"{ind} (high T&E complexity)"
    if ind == "Education":
        return 8, "Education (FBT pain point)"
    if ind == "Professional Services":
        return 5, "Professional Services"
    return 0, ""


def _news_signal(company: Company) -> tuple[int, str]:
    news = company.enrichment_signals.get("recent_news", [])
    if not news:
        return 0, ""
    text = " ".join(
        f"{n.get('title', '')} {n.get('description', '')}" for n in news
    ).lower()
    if "expansion" in text or "acquisition" in text:
        return 15, f"Expansion/acquisition news ({len(news)} articles)"
    return 0, ""


def _concur_job_signal(company: Company) -> tuple[int, str]:
    if company.enrichment_signals.get("concur_job_postings"):
        return 10, "Active Concur job postings (expanding usage)"
    return 0, ""


def _completeness_signal(company: Company) -> tuple[int, str]:
    pop, tot = completeness_score(company)
    pct = (pop / tot * 100) if tot else 0
    if pct >= 80:
        return 5, f"Data completeness {pop}/{tot} ({pct:.0f}%)"
    return 0, ""


_COMPANY_SIGNALS = [
    _concur_sap_signal,
    _overseas_signal,
    _headcount_signal,
    _industry_signal,
    _news_signal,
    _concur_job_signal,
    _completeness_signal,
]


def score_company(company: Company) -> int:
    """Compute company lead score (0-100)."""
    total = sum(fn(company)[0] for fn in _COMPANY_SIGNALS)
    return min(total, 100)


def get_company_breakdown(company: Company) -> dict[str, int]:
    """Return {signal_label: points} for every signal that contributed."""
    breakdown: dict[str, int] = {}
    for fn in _COMPANY_SIGNALS:
        pts, label = fn(company)
        if pts > 0 and label:
            breakdown[label] = pts
    return breakdown


# ---------------------------------------------------------------------------
# Contact scoring
# ---------------------------------------------------------------------------

_CONTACT_TYPE_POINTS: dict[str, tuple[int, str]] = {
    "CFO": (20, "CFO (top decision maker)"),
    "Finance Director": (15, "Finance Director"),
    "Financial Controller": (15, "Financial Controller"),
    "Head of Finance": (10, "Head of Finance"),
    "Finance Manager": (10, "Finance Manager"),
    "Digital Transformation": (12, "Digital Transformation lead"),
}


def _contact_type_signal(contact: Contact) -> tuple[int, str]:
    pts, label = _CONTACT_TYPE_POINTS.get(contact.contact_type, (0, ""))
    return pts, label


def _flow_type_signal(contact: Contact) -> tuple[int, str]:
    if contact.flow_type == "re_activation":
        return 15, "Re-activation (existing relationship)"
    return 0, ""


def _profile_signal(contact: Contact) -> tuple[int, str]:
    if contact.profile_summary and len(contact.profile_summary) > 50:
        return 5, "Rich profile summary available"
    return 0, ""


def _provider_id_signal(contact: Contact) -> tuple[int, str]:
    if contact.linkedin_provider_id:
        return 3, "DM-ready (provider ID resolved)"
    return 0, ""


_CONTACT_SIGNALS = [
    _contact_type_signal,
    _flow_type_signal,
    _profile_signal,
    _provider_id_signal,
]


def score_contact(contact: Contact, company: Company) -> int:
    """Compute contact lead score: 50% inherited from company + own signals."""
    company_part = int(score_company(company) * 0.5)
    own_part = sum(fn(contact)[0] for fn in _CONTACT_SIGNALS)
    return min(company_part + own_part, 100)


def get_contact_breakdown(contact: Contact, company: Company) -> dict[str, int]:
    """Return {signal_label: points} for a contact, including inherited company portion."""
    breakdown: dict[str, int] = {}
    company_score = score_company(company)
    inherited = int(company_score * 0.5)
    if inherited > 0:
        breakdown[f"Company score ({company_score}) × 50%"] = inherited
    for fn in _CONTACT_SIGNALS:
        pts, label = fn(contact)
        if pts > 0 and label:
            breakdown[label] = pts
    return breakdown


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def batch_score_companies(companies: list[Company]) -> list[tuple[Company, int]]:
    """Score a list of companies, returned sorted descending by score."""
    scored = [(c, score_company(c)) for c in companies]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def batch_score_contacts(
    contacts: list[Contact],
    companies_map: dict[str, Company],
) -> list[tuple[Contact, int]]:
    """Score contacts using a {company_name: Company} lookup. Sorted descending."""
    scored: list[tuple[Contact, int]] = []
    for ct in contacts:
        comp = companies_map.get(ct.company_name)
        if comp is None:
            # Fall-back: score with a minimal placeholder company
            comp = Company(
                company_name=ct.company_name,
                linkedin_url="",
                industry="Other",
            )
        scored.append((ct, score_contact(ct, comp)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
