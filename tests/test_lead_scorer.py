"""Tests for lead_scorer module."""

import pytest

from src.lead_scorer import (
    batch_score_companies,
    batch_score_contacts,
    get_company_breakdown,
    get_contact_breakdown,
    score_company,
    score_contact,
)
from src.models import Company, Contact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _co(**kw) -> Company:
    defaults = {
        "company_name": "TestCo",
        "linkedin_url": "https://linkedin.com/company/testco/",
        "industry": "Other",
    }
    defaults.update(kw)
    return Company(**defaults)


def _ct(**kw) -> Contact:
    defaults = {
        "name": "Jane",
        "title": "CFO",
        "company_name": "TestCo",
        "linkedin_url": "https://linkedin.com/in/jane/",
        "contact_type": "Other",
        "flow_type": "cold_new",
    }
    defaults.update(kw)
    return Contact(**defaults)


# ---------------------------------------------------------------------------
# Company scoring — SAP / Concur cross-signal
# ---------------------------------------------------------------------------

class TestCompanyConcurSAP:
    def test_sap_yes_concur_no_gives_30(self):
        c = _co(sap_user="Yes", uses_concur="No")
        bd = get_company_breakdown(c)
        assert bd.get("SAP user without Concur (ideal target)") == 30

    def test_sap_yes_concur_unknown_gives_20(self):
        c = _co(sap_user="Yes", uses_concur="Unknown")
        bd = get_company_breakdown(c)
        assert bd.get("SAP user, Concur unknown") == 20

    def test_concur_yes_gives_0(self):
        c = _co(uses_concur="Yes", sap_user="Yes")
        bd = get_company_breakdown(c)
        assert "Already uses Concur" not in bd  # 0 points → not in breakdown

    def test_sap_unknown_gives_5(self):
        c = _co(sap_user="Unknown", uses_concur="Unknown")
        bd = get_company_breakdown(c)
        assert bd.get("SAP status unknown") == 5


# ---------------------------------------------------------------------------
# Company scoring — overseas
# ---------------------------------------------------------------------------

class TestCompanyOverseas:
    def test_overseas_true_gives_20(self):
        c = _co(has_overseas_offices=True)
        bd = get_company_breakdown(c)
        assert bd.get("Has overseas offices (cross-border compliance)") == 20

    def test_overseas_false_gives_0(self):
        c = _co(has_overseas_offices=False)
        bd = get_company_breakdown(c)
        assert "Has overseas offices" not in str(bd)


# ---------------------------------------------------------------------------
# Company scoring — headcount tiers
# ---------------------------------------------------------------------------

class TestCompanyHeadcount:
    @pytest.mark.parametrize("count,expected", [
        (100, 0), (200, 5), (400, 5), (500, 10), (1999, 10),
        (2000, 15), (5000, 15), (6000, 0),
    ])
    def test_headcount_tiers(self, count, expected):
        c = _co(employee_count=count)
        total = score_company(c)
        base = score_company(_co(employee_count=0))
        assert total - base == expected


# ---------------------------------------------------------------------------
# Company scoring — industry
# ---------------------------------------------------------------------------

class TestCompanyIndustry:
    @pytest.mark.parametrize("ind,expected", [
        ("Manufacturing", 10), ("Mining", 10), ("Education", 8),
        ("Professional Services", 5), ("Resources", 0), ("Other", 0),
    ])
    def test_industry_points(self, ind, expected):
        c = _co(industry=ind)
        base = score_company(_co(industry="Other"))
        assert score_company(c) - base == expected


# ---------------------------------------------------------------------------
# Company scoring — news signal
# ---------------------------------------------------------------------------

class TestCompanyNews:
    def test_expansion_news_gives_15(self):
        c = _co(enrichment_signals={
            "recent_news": [{"title": "TestCo expansion into Asia", "url": "x"}]
        })
        bd = get_company_breakdown(c)
        assert any("Expansion" in k for k in bd)

    def test_no_expansion_keyword_gives_0(self):
        c = _co(enrichment_signals={
            "recent_news": [{"title": "TestCo Q1 results", "url": "x"}]
        })
        bd = get_company_breakdown(c)
        assert not any("Expansion" in k for k in bd)

    def test_no_news_gives_0(self):
        c = _co()
        bd = get_company_breakdown(c)
        assert not any("news" in k.lower() for k in bd)


# ---------------------------------------------------------------------------
# Company scoring — concur job postings
# ---------------------------------------------------------------------------

class TestCompanyConcurJobs:
    def test_concur_job_postings_gives_10(self):
        c = _co(enrichment_signals={
            "concur_job_postings": [{"title": "Concur Admin", "url": "x"}]
        })
        bd = get_company_breakdown(c)
        assert bd.get("Active Concur job postings (expanding usage)") == 10


# ---------------------------------------------------------------------------
# Company scoring — completeness
# ---------------------------------------------------------------------------

class TestCompanyCompleteness:
    def test_high_completeness_gives_5(self):
        c = _co(
            website="https://testco.com", employee_count=500,
            hq_city="Sydney", has_overseas_offices=True,
        )
        bd = get_company_breakdown(c)
        assert any("completeness" in k.lower() for k in bd)

    def test_low_completeness_gives_0(self):
        c = _co()
        bd = get_company_breakdown(c)
        assert not any("completeness" in k.lower() for k in bd)


# ---------------------------------------------------------------------------
# Company total score — golden scenario
# ---------------------------------------------------------------------------

class TestCompanyTotal:
    def test_ideal_target_score(self):
        """SAP+no Concur, overseas, 1000 employees, Manufacturing, expansion news, completeness"""
        c = _co(
            sap_user="Yes", uses_concur="No",
            has_overseas_offices=True,
            employee_count=1000,
            industry="Manufacturing",
            website="https://x.com", hq_city="Melbourne",
            enrichment_signals={
                "recent_news": [{"title": "expansion into NZ", "url": "x"}],
                "concur_job_postings": [{"title": "admin", "url": "y"}],
            },
        )
        s = score_company(c)
        # 30 + 20 + 10 + 10 + 15 + 10 + 5 = 100
        assert s == 100

    def test_minimal_company_not_zero(self):
        """Even unknown company gets SAP-unknown points."""
        c = _co()
        assert score_company(c) == 5  # sap_user="Unknown" default

    def test_score_capped_at_100(self):
        c = _co(
            sap_user="Yes", uses_concur="No",
            has_overseas_offices=True,
            employee_count=3000,
            industry="Mining",
            website="https://x.com", hq_city="Perth",
            enrichment_signals={
                "recent_news": [{"title": "acquisition deal", "url": "x"}],
                "concur_job_postings": [{"title": "admin", "url": "y"}],
            },
        )
        assert score_company(c) <= 100


# ---------------------------------------------------------------------------
# Contact scoring
# ---------------------------------------------------------------------------

class TestContactScoring:
    def test_cfo_cold_inherits_company(self):
        co = _co(sap_user="Yes", uses_concur="No", employee_count=1000, industry="Manufacturing")
        ct = _ct(contact_type="CFO", flow_type="cold_new")
        # company = 30 + 10 + 10 = 50 → inherited 25, + CFO 20 = 45
        s = score_contact(ct, co)
        assert s == 45

    def test_reactivation_bonus(self):
        co = _co()
        ct_cold = _ct(flow_type="cold_new")
        ct_react = _ct(flow_type="re_activation")
        assert score_contact(ct_react, co) - score_contact(ct_cold, co) == 15

    def test_profile_summary_bonus(self):
        co = _co()
        ct_no = _ct()
        ct_yes = _ct(profile_summary="A" * 51)
        assert score_contact(ct_yes, co) - score_contact(ct_no, co) == 5

    def test_provider_id_bonus(self):
        co = _co()
        ct_no = _ct()
        ct_yes = _ct(linkedin_provider_id="abc123")
        assert score_contact(ct_yes, co) - score_contact(ct_no, co) == 3

    def test_contact_breakdown_includes_company(self):
        co = _co(sap_user="Yes", uses_concur="No")  # company gets 30
        ct = _ct(contact_type="Finance Director", flow_type="re_activation")
        bd = get_contact_breakdown(ct, co)
        assert any("Company score" in k for k in bd)
        assert any("Finance Director" in k for k in bd)
        assert any("Re-activation" in k for k in bd)

    def test_score_capped_at_100(self):
        co = _co(
            sap_user="Yes", uses_concur="No",
            has_overseas_offices=True, employee_count=3000,
            industry="Mining",
            website="https://x.com", hq_city="Perth",
            enrichment_signals={
                "recent_news": [{"title": "acquisition deal", "url": "x"}],
                "concur_job_postings": [{"title": "admin", "url": "y"}],
            },
        )
        ct = _ct(
            contact_type="CFO", flow_type="re_activation",
            profile_summary="A" * 100, linkedin_provider_id="abc",
        )
        assert score_contact(ct, co) <= 100


# ---------------------------------------------------------------------------
# Contact type points
# ---------------------------------------------------------------------------

class TestContactTypes:
    @pytest.mark.parametrize("ctype,expected_pts", [
        ("CFO", 20), ("Finance Director", 15), ("Financial Controller", 15),
        ("Head of Finance", 10), ("Finance Manager", 10),
        ("Digital Transformation", 12), ("Other", 0),
    ])
    def test_contact_type_points(self, ctype, expected_pts):
        co = _co()
        ct_base = _ct(contact_type="Other")
        ct_test = _ct(contact_type=ctype)
        diff = score_contact(ct_test, co) - score_contact(ct_base, co)
        assert diff == expected_pts


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

class TestBatch:
    def test_batch_score_companies_sorted_descending(self):
        companies = [
            _co(company_name="Low", sap_user="No", uses_concur="No"),
            _co(company_name="High", sap_user="Yes", uses_concur="No", employee_count=3000),
            _co(company_name="Mid", sap_user="Yes", uses_concur="Unknown"),
        ]
        scored = batch_score_companies(companies)
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)
        assert scored[0][0].company_name == "High"

    def test_batch_score_contacts_with_missing_company(self):
        contacts = [_ct(company_name="Unknown Corp", contact_type="CFO")]
        scored = batch_score_contacts(contacts, {})
        assert len(scored) == 1
        # Should still produce a score (using fallback company)
        assert scored[0][1] > 0

    def test_batch_score_contacts_sorted_descending(self):
        co = _co()
        contacts = [
            _ct(name="Low", contact_type="Other", flow_type="cold_new"),
            _ct(name="High", contact_type="CFO", flow_type="re_activation"),
            _ct(name="Mid", contact_type="Finance Director", flow_type="cold_new"),
        ]
        scored = batch_score_contacts(contacts, {"TestCo": co})
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)
        assert scored[0][0].name == "High"


# ---------------------------------------------------------------------------
# CLI: pipeline scores --help
# ---------------------------------------------------------------------------

class TestScoresCLI:
    def test_help(self):
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["pipeline", "scores", "--help"])
        assert result.exit_code == 0
        assert "leaderboard" in result.output.lower() or "lead score" in result.output.lower()

    def test_aborts_without_feishu(self):
        from unittest.mock import MagicMock, patch
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        with patch("src.cli.load_settings") as mock:
            s = MagicMock()
            s.feishu_app_id = ""
            s.feishu_app_secret = ""
            s.feishu_bitable_app_token = ""
            s.feishu_companies_table_id = ""
            mock.return_value = s
            result = runner.invoke(app, ["pipeline", "scores"])
        assert result.exit_code == 1
