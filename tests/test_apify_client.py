"""Tests for ApifyLinkedInClient and the scrape-companies CLI command."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.apify_client import (
    ApifyLinkedInClient,
    _infer_contact_type,
    _infer_industry,
    _normalise_linkedin_url,
    completeness_score,
)
from src.models import Company, Contact

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
# _infer_contact_type
# ---------------------------------------------------------------------------


class TestInferContactType:
    def test_cfo(self):
        assert _infer_contact_type("CFO at Acme Corp") == "CFO"

    def test_chief_financial_officer(self):
        assert _infer_contact_type("Chief Financial Officer") == "CFO"

    def test_finance_director(self):
        assert _infer_contact_type("Finance Director - APAC") == "Finance Director"

    def test_financial_controller(self):
        assert _infer_contact_type("Financial Controller") == "Financial Controller"

    def test_head_of_finance(self):
        assert _infer_contact_type("Head of Finance | Acme") == "Head of Finance"

    def test_finance_manager(self):
        assert _infer_contact_type("Finance Manager") == "Finance Manager"

    def test_digital_transformation(self):
        assert _infer_contact_type("VP Digital Transformation") == "Digital Transformation"

    def test_unknown(self):
        assert _infer_contact_type("Software Engineer") == "Other"


# ---------------------------------------------------------------------------
# ApifyLinkedInClient._parse_contact
# ---------------------------------------------------------------------------


class TestParseContact:
    def setup_method(self):
        self.client = ApifyLinkedInClient(api_token="fake")

    def test_parses_full_item(self):
        raw = {
            "name": "John Doe",
            "linkedinUrl": "https://www.linkedin.com/in/johndoe?trk=1",
            "title": "CFO at Acme",
            "about": "Experienced CFO with 20 years...",
        }
        ct = self.client._parse_contact(raw, "Acme Corp")
        assert ct is not None
        assert ct.name == "John Doe"
        assert ct.linkedin_url == "https://www.linkedin.com/in/johndoe/"
        assert ct.contact_type == "CFO"
        assert ct.company_name == "Acme Corp"
        assert ct.flow_type == "cold_new"
        assert ct.profile_summary == "Experienced CFO with 20 years..."

    def test_builds_name_from_first_last(self):
        raw = {
            "firstName": "Jane",
            "lastName": "Smith",
            "linkedinUrl": "https://linkedin.com/in/janesmith",
            "headline": "Finance Director",
        }
        ct = self.client._parse_contact(raw, "TestCo")
        assert ct is not None
        assert ct.name == "Jane Smith"
        assert ct.contact_type == "Finance Director"

    def test_uses_headline_as_title(self):
        raw = {
            "name": "Bob",
            "linkedinUrl": "https://linkedin.com/in/bob",
            "headline": "Head of Finance | APAC",
        }
        ct = self.client._parse_contact(raw, "TestCo")
        assert ct is not None
        assert ct.title == "Head of Finance | APAC"
        assert ct.contact_type == "Head of Finance"

    def test_uses_profileUrl_field(self):
        raw = {
            "name": "Alice",
            "profileUrl": "https://linkedin.com/in/alice",
            "title": "Finance Manager",
        }
        ct = self.client._parse_contact(raw, "TestCo")
        assert ct is not None
        assert ct.linkedin_url == "https://linkedin.com/in/alice/"

    def test_skips_no_url(self):
        raw = {"name": "NoURL"}
        assert self.client._parse_contact(raw, "TestCo") is None

    def test_skips_no_name(self):
        raw = {"linkedinUrl": "https://linkedin.com/in/x"}
        assert self.client._parse_contact(raw, "TestCo") is None


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


# ---------------------------------------------------------------------------
# CLI integration: scrape-contacts
# ---------------------------------------------------------------------------


class TestScrapeContactsCLI:
    def _make_settings(self, **overrides):
        s = MagicMock()
        s.apify_api_token = "fake_token"
        s.feishu_app_id = "fid"
        s.feishu_app_secret = "fsec"
        s.feishu_bitable_app_token = "fatok"
        s.feishu_companies_table_id = "tbl_co"
        s.feishu_contacts_table_id = "tbl_ct"
        s.unipile_api_key = ""
        s.unipile_dsn = ""
        s.unipile_account_id = ""
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_aborts_without_apify(self):
        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = self._make_settings(apify_api_token="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "scrape-contacts", "--dry-run"])
        assert result.exit_code == 1
        assert "APIFY_API_TOKEN" in result.output

    def test_aborts_without_feishu(self):
        with patch("src.cli.load_settings") as mock_settings:
            mock_settings.return_value = self._make_settings(feishu_app_id="")
            from src.cli import app
            result = runner.invoke(app, ["pipeline", "scrape-contacts", "--dry-run"])
        assert result.exit_code == 1

    def test_dry_run_scrape_contacts(self):
        """End-to-end dry-run: mock Apify + Bitable, verify output."""
        mock_contacts = [
            Contact(
                name="John Doe", title="CFO", company_name="Acme",
                linkedin_url="https://linkedin.com/in/johndoe/",
                contact_type="CFO", flow_type="cold_new",
            ),
        ]
        mock_company_records = [
            {
                "record_id": "r1",
                "fields": {
                    "公司名称": "Acme",
                    "LinkedIn URL": "https://linkedin.com/company/acme/",
                    "行业": "Manufacturing",
                    "触达状态": "未触达",
                    "来源": "Apify",
                    "Lead评分": 50,
                },
            }
        ]

        with patch("src.cli.load_settings") as mock_settings, \
             patch("src.apify_client.ApifyClient"), \
             patch.object(ApifyLinkedInClient, "scrape_contacts_for_company", return_value=mock_contacts):

            mock_settings.return_value = self._make_settings()

            # Mock Bitable client methods
            from src.bitable_client import BitableClient
            with patch.object(BitableClient, "_refresh_token", return_value="fake"), \
                 patch.object(BitableClient, "list_records", return_value=mock_company_records), \
                 patch.object(BitableClient, "find_contact_by_linkedin_url", return_value=None), \
                 patch.object(BitableClient, "batch_create_records", return_value=[]):

                from src.cli import app
                result = runner.invoke(app, [
                    "pipeline", "scrape-contacts",
                    "--dry-run",
                ])

        assert result.exit_code == 0
        assert "Acme" in result.output
        assert "1 new" in result.output or "new contacts" in result.output.lower()
