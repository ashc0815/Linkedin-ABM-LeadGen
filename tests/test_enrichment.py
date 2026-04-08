"""Tests for enrichment module and pipeline enrich CLI command."""

from unittest.mock import MagicMock, patch

import pytest

from src.enrichment import (
    BraveSearchClient,
    EnrichmentService,
    _step_abn_verification,
    _step_company_news,
    _step_job_postings,
    _step_tech_stack,
    _step_website_analysis,
    enrichment_summary,
)
from src.models import Company
from src.rate_limiter import RateLimiter


def _make_company(**overrides) -> Company:
    defaults = {
        "company_name": "TestCo",
        "linkedin_url": "https://linkedin.com/company/testco/",
        "industry": "Manufacturing",
    }
    defaults.update(overrides)
    return Company(**defaults)


# ---------------------------------------------------------------------------
# BraveSearchClient
# ---------------------------------------------------------------------------


class TestBraveSearchClient:
    def test_search_returns_parsed_results(self):
        client = BraveSearchClient(api_key="fake", rate_limiter=RateLimiter())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "web": {
                "results": [
                    {"title": "Result 1", "url": "https://example.com/1", "description": "Desc 1"},
                    {"title": "Result 2", "url": "https://example.com/2", "description": "Desc 2"},
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._http, "get", return_value=mock_resp):
            results = client.search("test query", count=2)

        assert len(results) == 2
        assert results[0]["title"] == "Result 1"

    def test_search_returns_empty_on_error(self):
        client = BraveSearchClient(api_key="fake", rate_limiter=RateLimiter())

        with patch.object(client._http, "get", side_effect=Exception("network error")):
            results = client.search("test query")

        assert results == []


# ---------------------------------------------------------------------------
# Enrichment steps
# ---------------------------------------------------------------------------


class TestStepJobPostings:
    def test_concur_detected(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.side_effect = [
            [{"title": "Concur Admin job", "url": "https://seek.com.au/job/1"}],  # concur
            [],  # sap
        ]
        company = _make_company()
        result = _step_job_postings(company, brave)
        assert result.uses_concur == "Yes"
        assert "concur_job_postings" in result.enrichment_signals
        assert result.sap_user == "Unknown"

    def test_sap_detected(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.side_effect = [
            [],  # concur
            [{"title": "SAP ERP Consultant", "url": "https://seek.com.au/job/2"}],  # sap
        ]
        company = _make_company()
        result = _step_job_postings(company, brave)
        assert result.sap_user == "Yes"
        assert result.uses_concur == "Unknown"

    def test_no_results(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = []
        company = _make_company()
        result = _step_job_postings(company, brave)
        assert result.sap_user == "Unknown"
        assert result.uses_concur == "Unknown"


class TestStepTechStack:
    def test_extracts_keywords(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = [
            {"title": "TestCo SAP Implementation", "url": "https://x.com", "description": "NetSuite migration"}
        ]
        company = _make_company()
        result = _step_tech_stack(company, brave)
        clues = result.enrichment_signals.get("tech_stack_clues", [])
        assert "sap" in clues
        assert "netsuite" in clues


class TestStepCompanyNews:
    def test_finds_news_and_expansion_signal(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = [
            {"title": "TestCo expansion into Asia", "url": "https://news.com/1", "description": "International expansion announced"},
            {"title": "TestCo Q1 results", "url": "https://news.com/2", "description": "Revenue up 20%"},
        ]
        company = _make_company()
        result = _step_company_news(company, brave)
        assert len(result.enrichment_signals["recent_news"]) == 2
        assert result.has_overseas_offices is True

    def test_no_news(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = []
        company = _make_company()
        result = _step_company_news(company, brave)
        assert "recent_news" not in result.enrichment_signals


class TestStepWebsiteAnalysis:
    def test_detects_country_paths(self):
        company = _make_company(website="https://testco.com")
        mock_resp = MagicMock()
        mock_resp.text = '<a href="/au/">Australia</a> <a href="/nz/">New Zealand</a> /careers page'

        with patch("src.enrichment.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            result = _step_website_analysis(company)

        assert result.has_overseas_offices is True
        signals = result.enrichment_signals.get("website_signals", {})
        assert "/au/" in signals.get("country_paths", [])

    def test_skips_without_website(self):
        company = _make_company(website=None)
        result = _step_website_analysis(company)
        assert "website_signals" not in result.enrichment_signals

    def test_handles_timeout(self):
        company = _make_company(website="https://testco.com")
        with patch("src.enrichment.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(side_effect=Exception("timeout")))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            result = _step_website_analysis(company)
        assert "website_signals" not in result.enrichment_signals


class TestStepABNVerification:
    def test_extracts_abn(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = [
            {"title": "TestCo Pty Ltd", "url": "https://abr.business.gov.au/x",
             "description": "ABN: 12 345 678 901 - Active"}
        ]
        company = _make_company()
        result = _step_abn_verification(company, brave)
        assert result.enrichment_signals["abn"] == "12345678901"

    def test_no_abn_found(self):
        brave = MagicMock(spec=BraveSearchClient)
        brave.search.return_value = []
        company = _make_company()
        result = _step_abn_verification(company, brave)
        assert "abn" not in result.enrichment_signals


# ---------------------------------------------------------------------------
# EnrichmentService
# ---------------------------------------------------------------------------


class TestEnrichmentService:
    def test_runs_all_steps_without_crashing(self):
        """Even if Brave returns nothing, all steps complete."""
        with patch.object(BraveSearchClient, "search", return_value=[]):
            service = EnrichmentService(brave_api_key="fake")
            company = _make_company(website="https://example.com")

            with patch("src.enrichment.httpx.Client") as MockClient:
                MockClient.return_value.__enter__ = MagicMock(
                    return_value=MagicMock(get=MagicMock(side_effect=Exception("no network")))
                )
                MockClient.return_value.__exit__ = MagicMock(return_value=False)
                result = service.enrich_company(company)

        assert result.company_name == "TestCo"

    def test_step_failure_does_not_block_others(self):
        """If one step raises, the rest still run."""
        call_log = []

        def mock_search(query, count=5):
            call_log.append(query)
            if "SAP Concur" in query:
                raise RuntimeError("boom")
            return []

        with patch.object(BraveSearchClient, "search", side_effect=mock_search):
            service = EnrichmentService(brave_api_key="fake")
            company = _make_company()
            result = service.enrich_company(company)

        # Should have attempted multiple searches despite first step failing
        assert len(call_log) >= 2
        assert result.company_name == "TestCo"


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


class TestEnrichmentSummary:
    def test_summary_with_signals(self):
        company = _make_company()
        company.uses_concur = "Yes"
        company.sap_user = "Yes"
        company.enrichment_signals = {
            "concur_job_postings": [{"title": "x", "url": "y"}],
            "recent_news": [{"title": "News 1", "url": "z"}],
        }
        summary = enrichment_summary(company)
        assert "Concur confirmed" in summary
        assert "SAP confirmed" in summary
        assert "1 news items" in summary

    def test_summary_no_signals(self):
        company = _make_company()
        summary = enrichment_summary(company)
        assert "no strong signals" in summary


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestPipelineEnrichCLI:
    def test_aborts_without_brave_key(self):
        from typer.testing import CliRunner
        from src.cli import app

        runner = CliRunner()
        with patch("src.cli.load_settings") as mock_settings:
            s = MagicMock()
            s.brave_search_api_key = ""
            mock_settings.return_value = s

            result = runner.invoke(app, ["pipeline", "enrich"])

        assert result.exit_code == 1
        assert "BRAVE_SEARCH_API_KEY" in result.output

    def test_aborts_without_feishu(self):
        from typer.testing import CliRunner
        from src.cli import app

        runner = CliRunner()
        with patch("src.cli.load_settings") as mock_settings:
            s = MagicMock()
            s.brave_search_api_key = "fake"
            s.feishu_app_id = ""
            s.feishu_app_secret = ""
            s.feishu_bitable_app_token = ""
            s.feishu_companies_table_id = ""
            mock_settings.return_value = s

            result = runner.invoke(app, ["pipeline", "enrich"])

        assert result.exit_code == 1
        assert "Feishu" in result.output or "Bitable" in result.output
