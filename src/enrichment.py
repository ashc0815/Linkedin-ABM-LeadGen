"""Data enrichment: cross-reference company data via Brave Search and website analysis."""

from __future__ import annotations

import logging
import re

import httpx

from src.models import Company
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Country path fragments that signal overseas offices
_COUNTRY_PATHS = [
    "/au/", "/nz/", "/sg/", "/hk/", "/uk/", "/us/", "/in/",
    "/my/", "/jp/", "/cn/", "/de/", "/fr/", "/id/", "/ph/",
]


# ---------------------------------------------------------------------------
# Brave Search Client
# ---------------------------------------------------------------------------


class BraveSearchClient:
    """Thin wrapper around Brave Web Search API with built-in rate limiting."""

    def __init__(self, api_key: str, rate_limiter: RateLimiter | None = None) -> None:
        self.api_key = api_key
        self._http = httpx.Client(timeout=15.0)
        self._limiter = rate_limiter or RateLimiter(max_calls_per_second=1)

    def search(self, query: str, count: int = 5) -> list[dict]:
        """Execute a Brave web search. Returns list of result dicts with keys: title, url, description."""
        self._limiter.wait()
        try:
            resp = self._http.get(
                BRAVE_SEARCH_URL,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                params={"q": query, "count": count},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Brave search failed for %r: %s", query, exc)
            return []

        results: list[dict] = []
        for item in data.get("web", {}).get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
            })
        return results


# ---------------------------------------------------------------------------
# Enrichment steps
# ---------------------------------------------------------------------------


def _step_job_postings(company: Company, brave: BraveSearchClient) -> Company:
    """Step 1: Check job postings for Concur / SAP signals."""
    name = company.company_name

    # Concur check
    query = f'"{name}" "SAP Concur" OR "Concur administrator" site:seek.com.au OR site:linkedin.com/jobs'
    results = brave.search(query, count=3)
    if results:
        company.uses_concur = "Yes"
        company.enrichment_signals["concur_job_postings"] = [
            {"title": r["title"], "url": r["url"]} for r in results
        ]
        logger.info("%s: Concur confirmed via job posting", name)

    # SAP check
    query = f'"{name}" "SAP" OR "SAP ERP" OR "S/4HANA" site:seek.com.au'
    results = brave.search(query, count=3)
    if results:
        company.sap_user = "Yes"
        company.enrichment_signals["sap_job_postings"] = [
            {"title": r["title"], "url": r["url"]} for r in results
        ]
        logger.info("%s: SAP confirmed via job posting", name)

    return company


def _step_tech_stack(company: Company, brave: BraveSearchClient) -> Company:
    """Step 2: Look for ERP / tech stack clues in annual reports and articles."""
    name = company.company_name
    query = f'"{name}" "SAP" OR "Oracle" OR "NetSuite" OR "Workday" Australia annual report'
    results = brave.search(query, count=5)

    clues: list[str] = []
    keywords = ["sap", "oracle", "netsuite", "workday", "s/4hana", "concur"]
    for r in results:
        text = f"{r['title']} {r['description']}".lower()
        for kw in keywords:
            if kw in text and kw not in clues:
                clues.append(kw)

    if clues:
        company.enrichment_signals["tech_stack_clues"] = clues
        logger.info("%s: tech stack clues — %s", name, ", ".join(clues))

    return company


def _step_company_news(company: Company, brave: BraveSearchClient) -> Company:
    """Step 3: Find recent news (expansion, M&A) for DM conversation starters."""
    name = company.company_name
    query = f'"{name}" expansion OR acquisition OR "new office" OR merger 2025 2026'
    results = brave.search(query, count=5)

    news_items = [{"title": r["title"], "url": r["url"]} for r in results[:3]]
    if news_items:
        company.enrichment_signals["recent_news"] = news_items
        logger.info("%s: %d news items found", name, len(news_items))

        # Check for expansion signals
        for r in results:
            text = f"{r['title']} {r['description']}".lower()
            if any(kw in text for kw in ("expansion", "new office", "overseas", "international")):
                company.has_overseas_offices = True
                logger.info("%s: overseas expansion signal detected", name)
                break

    return company


def _step_website_analysis(company: Company) -> Company:
    """Step 4: Analyse company website for multi-country presence."""
    if not company.website:
        return company

    signals: dict[str, object] = {}
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as http:
            resp = http.get(company.website)
            body = resp.text.lower()

            # Check for country paths in links
            countries_found = [p for p in _COUNTRY_PATHS if p in body]
            if countries_found:
                company.has_overseas_offices = True
                signals["country_paths"] = countries_found

            # Check for careers page link
            if "/career" in body or "/jobs" in body:
                signals["has_careers_page"] = True

    except Exception as exc:
        logger.debug("%s: website analysis skipped — %s", company.company_name, exc)
        return company

    if signals:
        company.enrichment_signals["website_signals"] = signals
        logger.info("%s: website signals — %s", company.company_name, signals)

    return company


def _step_abn_verification(company: Company, brave: BraveSearchClient) -> Company:
    """Step 5: Look up Australian Business Number."""
    name = company.company_name
    query = f'"{name}" ABN site:abr.business.gov.au'
    results = brave.search(query, count=1)

    if results:
        # Try to extract ABN (11-digit number) from description
        text = results[0].get("description", "") + " " + results[0].get("title", "")
        abn_match = re.search(r"\b(\d{2}\s?\d{3}\s?\d{3}\s?\d{3})\b", text)
        if abn_match:
            company.enrichment_signals["abn"] = abn_match.group(1).replace(" ", "")
            logger.info("%s: ABN found — %s", name, company.enrichment_signals["abn"])
        else:
            company.enrichment_signals["abn_page"] = results[0]["url"]

    return company


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class EnrichmentService:
    """Orchestrate all enrichment steps for a company."""

    def __init__(self, brave_api_key: str) -> None:
        self._brave = BraveSearchClient(
            api_key=brave_api_key,
            rate_limiter=RateLimiter(max_calls_per_second=1),
        )

    def enrich_company(self, company: Company) -> Company:
        """Run all enrichment steps. Each step is independent; failures are isolated."""
        name = company.company_name
        logger.info("Starting enrichment for %s", name)

        steps = [
            ("job_postings", lambda c: _step_job_postings(c, self._brave)),
            ("tech_stack", lambda c: _step_tech_stack(c, self._brave)),
            ("company_news", lambda c: _step_company_news(c, self._brave)),
            ("website_analysis", lambda c: _step_website_analysis(c)),
            ("abn_verification", lambda c: _step_abn_verification(c, self._brave)),
        ]

        for step_name, step_fn in steps:
            try:
                company = step_fn(company)
            except Exception as exc:
                logger.error("%s: step '%s' failed — %s", name, step_name, exc)

        logger.info("Enrichment complete for %s: %s", name, _summary_line(company))
        return company


def _summary_line(company: Company) -> str:
    """Build a one-line enrichment summary for logging / CLI output."""
    parts: list[str] = []

    if company.uses_concur == "Yes":
        source = "job posting" if "concur_job_postings" in company.enrichment_signals else "signal"
        parts.append(f"Concur confirmed ({source})")
    if company.sap_user == "Yes":
        parts.append("SAP confirmed")

    news = company.enrichment_signals.get("recent_news", [])
    if news:
        parts.append(f"{len(news)} news items")

    ws = company.enrichment_signals.get("website_signals", {})
    if isinstance(ws, dict) and ws.get("country_paths"):
        paths = ", ".join(ws["country_paths"])
        parts.append(f"website has {paths} offices")
    elif company.has_overseas_offices:
        parts.append("overseas offices detected")

    if not parts:
        parts.append("no strong signals")

    return "; ".join(parts)


def enrichment_summary(company: Company) -> str:
    """Public wrapper for CLI display."""
    return _summary_line(company)
