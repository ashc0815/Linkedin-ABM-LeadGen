"""Apify integration for LinkedIn company scraping."""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse, urlunparse

from apify_client import ApifyClient

from src.models import Company

logger = logging.getLogger(__name__)

# Actor ID – swap if you use a different LinkedIn company search actor
COMPANY_SEARCH_ACTOR = "curious_coder/linkedin-company-search"

# Industries that we can infer from description keywords
_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "Manufacturing": ["manufactur", "factory", "production", "industrial"],
    "Professional Services": ["consult", "advisory", "professional service", "accounting", "legal"],
    "Education": ["university", "education", "school", "training", "academic"],
    "Mining": ["mining", "mineral", "extraction"],
    "Resources": ["energy", "oil", "gas", "resource", "utilities"],
}

# Fields considered for data completeness scoring
_COMPLETENESS_FIELDS = [
    "company_name", "linkedin_url", "website", "industry",
    "employee_count", "hq_city", "has_overseas_offices",
]


def _normalise_linkedin_url(url: str) -> str:
    """Strip query params, ensure trailing slash, lowercase domain."""
    parsed = urlparse(url)
    clean = urlunparse((
        parsed.scheme or "https",
        parsed.netloc.lower(),
        parsed.path.rstrip("/") + "/",
        "", "", "",
    ))
    return clean


def _infer_industry(description: str) -> str:
    """Try to infer industry from free-text description."""
    text = description.lower()
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return industry
    return "Other"


def _completeness_score(company: Company) -> tuple[int, int]:
    """Return (populated, total) for completeness fields."""
    total = len(_COMPLETENESS_FIELDS)
    populated = 0
    for f in _COMPLETENESS_FIELDS:
        val = getattr(company, f)
        if val is not None and val != 0 and val != "" and val is not False:
            populated += 1
    return populated, total


class ApifyLinkedInClient:
    """Wrapper around apify-client SDK for LinkedIn company search."""

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token
        self._client = ApifyClient(token=api_token)

    def scrape_companies(
        self,
        keyword: str,
        location: str = "Australia",
        min_employees: int = 200,
        max_employees: int = 5000,
        max_results: int = 100,
    ) -> list[Company]:
        """Run the LinkedIn company search actor and return parsed Company models."""
        run_input = {
            "keyword": keyword,
            "location": location,
            "minEmployees": min_employees,
            "maxEmployees": max_employees,
            "maxResults": max_results,
        }
        logger.info(
            "Starting Apify actor %s — keyword=%r location=%r employees=%d-%d max=%d",
            COMPANY_SEARCH_ACTOR, keyword, location, min_employees, max_employees, max_results,
        )

        run = self._client.actor(COMPANY_SEARCH_ACTOR).call(run_input=run_input)
        items = list(self._client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("Apify returned %d raw results for keyword=%r", len(items), keyword)

        companies: list[Company] = []
        for item in items:
            company = self._parse_company(item)
            if company is not None:
                companies.append(company)

        return companies

    def _parse_company(self, raw: dict) -> Company | None:
        """Map Apify raw item to Company model with data quality handling."""
        linkedin_url = raw.get("linkedinUrl") or raw.get("url") or raw.get("linkedin_url")
        if not linkedin_url:
            logger.warning("Skipping result without LinkedIn URL: %s", raw.get("name", "unknown"))
            return None

        linkedin_url = _normalise_linkedin_url(linkedin_url)
        company_name = raw.get("name") or raw.get("companyName") or ""
        if not company_name:
            logger.warning("Skipping result without company name: %s", linkedin_url)
            return None

        # Employee count
        employee_count = raw.get("employeeCount") or raw.get("employees") or 0
        notes_parts: list[str] = []
        if not employee_count:
            employee_count = 0
            notes_parts.append("headcount_missing")

        # Ensure int
        if isinstance(employee_count, str):
            employee_count = int(re.sub(r"[^\d]", "", employee_count) or "0")

        # Industry
        industry = raw.get("industry") or ""
        if industry not in (
            "Manufacturing", "Professional Services", "Education",
            "Mining", "Resources", "Other",
        ):
            description = raw.get("description") or raw.get("about") or ""
            industry = _infer_industry(f"{industry} {description}")

        # HQ city
        hq_city = raw.get("city") or raw.get("headquarters") or raw.get("location") or None

        # Website
        website = raw.get("website") or raw.get("websiteUrl") or None

        company = Company(
            company_name=company_name,
            linkedin_url=linkedin_url,
            website=website,
            industry=industry,
            employee_count=employee_count,
            hq_city=hq_city,
            source="Apify",
            notes=", ".join(notes_parts) if notes_parts else "",
        )

        populated, total = _completeness_score(company)
        logger.debug("Company %s: %d/%d fields populated", company_name, populated, total)
        return company

    # Stubs for future use
    def scrape_employees(self, company_url: str, title_keywords: list[str]) -> list[dict]:
        raise NotImplementedError

    def search_people(self, query: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError


def completeness_score(company: Company) -> tuple[int, int]:
    """Public helper for use in CLI output."""
    return _completeness_score(company)
