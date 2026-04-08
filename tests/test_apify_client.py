"""Tests for ApifyLinkedInClient and the scrape-companies CLI command."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.apify_client import (
    ApifyLinkedInClient,
    _infer_industry,
    _normalise_linkedin_url,
    completeness_score,
)
from src.models import Company

runner = CliRunner()

# ---------------------------------------------------------------------------
# Unit tests – helper functions
# ---------------------------------------------------------------------------


class TestNormaliseUrl:
    def test_strips_query_params(self):
        url = "https://www.linkedin.com/company/acme?trk=abc"
        assert _normalise_linkedin_url(url) == "https://www.linkedin.com/company/acme/"

    def test_adds_trailing_slash(self):
        url = "https://www.linkedin.com/company/acme"
        assert _normalise_linkedin_url(url) == "https://www.linkedin.com/company/acme/"

    def test_lowercases_domain(self):
        url = "https://WWW.LinkedIn.COM/company/Acme/"
        assert _normalise_linkedin_url(url) == "https://www.linkedin.com/company/Acme/"

    def test_already_normalised(self):
        url = "https://www.linkedin.com/company/acme/"
        assert _normalise_linkedin_url(url) == url


class TestInferIndustry:
    def test_manufacturing(self):
        assert _infer_industry("global manufacturing leader") == "Manufacturing"

    def test_education(self):
        assert _infer_industry("top university in Australia") == "Education"

    def test_mining(self):
        assert _infer_industry("mineral extraction and mining") == "Mining"

    def test_unknown_returns_other(self):
        assert _infer_industry("something random") == "Other"


class TestCompletenessScore:
    def test_full_company(self):
        c = Company(
            company_name="X", linkedin_url="https://li.com/co/x/",
            website="https://x.com", industry="Other",
            employee_count=100, hq_city="Sydney", has_overseas_offices=True,
        )
        pop, tot = completeness_score(c)
        assert pop == tot == 7

    def test_minimal_company(self):
        c = Company(
            company_name="X", linkedin_url="https://li.com/co/x/",
            industry="Other",
        )
        pop, tot = completeness_score(c)
        assert pop == 3  # company_name, linkedin_url, industry (Other counts)
        assert tot == 7


# ---------------------------------------------------------------------------
# ApifyLinkedInClient._parse_company
# ---------------------------------------------------------------------------


class TestParseCompany:
    def setup_method(self):
        self.client = ApifyLinkedInClient(api_token="fake")

    def test_parses_full_item(self):
        raw = {
            "name": "Acme Corp",
            "linkedinUrl": "https://www.linkedin.com/company/acme?trk=1",
            "website": "https://acme.com",
            "industry": "Manufacturing",
            "employeeCount": 1200,
            "city": "Melbourne",
        }
        company = self.client._parse_company(raw)
        assert company is not None
        assert company.company_name == "Acme Corp"
        assert company.linkedin_url == "https://www.linkedin.com/company/acme/"
        assert company.employee_count == 1200
        assert company.industry == "Manufacturing"
        assert company.source == "Apify"

    def test_missing_employee_count(self):
        raw = {
            "name": "NoCo",
            "linkedinUrl": "https://linkedin.com/company/noco",
            "industry": "Other",
        }
        company = self.client._parse_company(raw)
        assert company is not None
        assert company.employee_count == 0
        assert "headcount_missing" in company.notes

    def test_infers_industry_from_description(self):
        raw = {
            "name": "MineX",
            "linkedinUrl": "https://linkedin.com/company/minex",
            "industry": "SomethingWeird",
            "description": "Leading mineral extraction company",
        }
        company = self.client._parse_company(raw)
        assert company is not None
        assert company.industry == "Mining"

    def test_skips_no_url(self):
        raw = {"name": "NoURL"}
        assert self.client._parse_company(raw) is None

    def test_skips_no_name(self):
        raw = {"linkedinUrl": "https://linkedin.com/company/x"}
        assert self.client._parse_company(raw) is None

    def test_string_employee_count(self):
        raw = {
            "name": "StrCo",
            "linkedinUrl": "https://linkedin.com/company/strco",
            "industry": "Other",
            "employeeCount": "1,500 employees",
        }
        company = self.client._parse_company(raw)
        assert company is not None
        assert company.employee_count == 1500


# ---------------------------------------------------------------------------
# CLI integration: scrape-companies --dry-run
# ---------------------------------------------------------------------------


class TestScrapeCompaniesCLI:
    def test_dry_run_with_mocked_apify(self):
        """End-to-end dry-run: mock Apify, verify rich output."""
        mock_companies = [
            Company(
                company_name="TestCorp",
                linkedin_url="https://linkedin.com/company/testcorp/",
                industry="Manufacturing",
                employee_count=300,
                hq_city="Sydney",
                source="Apify",
            ),
            Company(
                company_name="DupeCorp",
                linkedin_url="https://linkedin.com/company/dupecorp/",
                industry="Education",
                employee_count=0,
                source="Apify",
                notes="headcount_missing",
            ),
        ]

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.apify_client.ApifyClient"):
            settings = MagicMock()
            settings.apify_api_token = "fake_token"
            settings.feishu_app_id = ""
            settings.feishu_app_secret = ""
            settings.feishu_bitable_app_token = ""
            settings.feishu_companies_table_id = ""
            settings.feishu_contacts_table_id = ""
            mock_settings.return_value = settings

            with patch.object(
                ApifyLinkedInClient, "scrape_companies", return_value=mock_companies
            ):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "scrape-companies",
                    "--industry", "Manufacturing",
                    "--location", "Australia",
                    "--dry-run",
                ])

        assert result.exit_code == 0
        assert "TestCorp" in result.output
        assert "DupeCorp" in result.output
        assert "dry-run" in result.output.lower() or "--dry-run" in result.output
        assert "新增 2 家" in result.output

    def test_aborts_without_apify_token(self):
        with patch("src.cli.load_settings") as mock_settings:
            settings = MagicMock()
            settings.apify_api_token = ""
            mock_settings.return_value = settings

            from src.cli import app
            result = runner.invoke(app, ["pipeline", "scrape-companies", "--dry-run"])

        assert result.exit_code == 1
        assert "APIFY_API_TOKEN" in result.output

    def test_empty_apify_results_warns(self):
        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.apify_client.ApifyClient"):
            settings = MagicMock()
            settings.apify_api_token = "fake"
            settings.feishu_app_id = ""
            settings.feishu_app_secret = ""
            settings.feishu_bitable_app_token = ""
            settings.feishu_companies_table_id = ""
            settings.feishu_contacts_table_id = ""
            mock_settings.return_value = settings

            with patch.object(
                ApifyLinkedInClient, "scrape_companies", return_value=[]
            ):
                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "scrape-companies",
                    "--industry", "XYZ",
                    "--dry-run",
                ])

        assert result.exit_code == 0
        assert "No results" in result.output or "no results" in result.output.lower()
