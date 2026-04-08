"""Tests for dm_generator module and pipeline generate-dms CLI."""

from unittest.mock import MagicMock, patch

import pytest

from src.dm_generator import (
    DMGenerator,
    _build_context_block,
    _build_shared_prompt,
    _get_style,
    _load_style,
    _validate_dm,
    make_draft_entry,
)
from src.models import Company, Contact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _co(**kw) -> Company:
    defaults = {
        "company_name": "Acme Corp",
        "linkedin_url": "https://linkedin.com/company/acme/",
        "industry": "Manufacturing",
    }
    defaults.update(kw)
    return Company(**defaults)


def _ct(**kw) -> Contact:
    defaults = {
        "name": "Jane Smith",
        "title": "CFO",
        "company_name": "Acme Corp",
        "linkedin_url": "https://linkedin.com/in/janesmith/",
        "contact_type": "CFO",
        "flow_type": "re_activation",
    }
    defaults.update(kw)
    return Contact(**defaults)


@pytest.fixture()
def style():
    return _get_style()


# ---------------------------------------------------------------------------
# YAML style loading
# ---------------------------------------------------------------------------


class TestStyleLoading:
    def test_loads_yaml(self):
        s = _get_style()
        assert "persona" in s
        assert "voice" in s
        assert "touch_strategy" in s

    def test_persona_fields(self):
        s = _get_style()
        assert s["persona"]["name"] == "Tony"
        assert "Hitpoint" in s["persona"]["company"]

    def test_blacklist_in_voice(self):
        s = _get_style()
        blacklist = [p.lower() for p in s["voice"]["blacklist"]]
        assert "leverage" in blacklist
        assert "synergy" in blacklist

    def test_industry_angles(self):
        s = _get_style()
        assert "Manufacturing" in s["industry_angles"]
        assert "Mining" in s["industry_angles"]

    def test_touch_strategy_both_flows(self):
        s = _get_style()
        assert "re_activation" in s["touch_strategy"]
        assert "cold_new" in s["touch_strategy"]
        for flow in ("re_activation", "cold_new"):
            for touch in ("day1", "day7", "day14", "day21"):
                assert touch in s["touch_strategy"][flow], f"Missing {flow}/{touch}"


# ---------------------------------------------------------------------------
# _build_shared_prompt
# ---------------------------------------------------------------------------


class TestBuildSharedPrompt:
    def test_includes_persona(self, style):
        prompt = _build_shared_prompt(style)
        assert "Tony" in prompt
        assert "Hitpoint" in prompt

    def test_includes_rules(self, style):
        prompt = _build_shared_prompt(style)
        assert "Australian English" in prompt
        assert "leverage" in prompt.lower()


# ---------------------------------------------------------------------------
# _build_context_block
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_includes_industry_pain_points(self, style):
        ctx = _build_context_block(_ct(), _co(industry="Manufacturing"), style)
        assert "cross-border" in ctx

    def test_includes_mining_pain_points(self, style):
        ctx = _build_context_block(_ct(), _co(industry="Mining"), style)
        assert "FIFO" in ctx

    def test_includes_education_pain_points(self, style):
        ctx = _build_context_block(_ct(), _co(industry="Education"), style)
        assert "FBT" in ctx

    def test_concur_yes_angle(self, style):
        ctx = _build_context_block(_ct(), _co(uses_concur="Yes"), style)
        assert "optimisation" in ctx.lower() or "health" in ctx.lower()

    def test_sap_yes_no_concur_angle(self, style):
        ctx = _build_context_block(_ct(), _co(sap_user="Yes", uses_concur="No"), style)
        assert "natural extension" in ctx

    def test_overseas_offices(self, style):
        ctx = _build_context_block(_ct(), _co(has_overseas_offices=True), style)
        assert "cross-border" in ctx.lower() or "Cross-border" in ctx

    def test_recent_news(self, style):
        co = _co(enrichment_signals={"recent_news": [{"title": "Acme acquires XYZ"}]})
        ctx = _build_context_block(_ct(), co, style)
        assert "Acme acquires XYZ" in ctx

    def test_expansion_detected(self, style):
        co = _co(enrichment_signals={
            "recent_news": [{"title": "Acme expansion into Asia", "description": "new offices"}]
        })
        ctx = _build_context_block(_ct(), co, style)
        assert "scaling" in ctx.lower() or "Scaling" in ctx

    def test_contact_profile_included(self, style):
        ct = _ct(profile_summary="20 years in finance across APAC")
        ctx = _build_context_block(ct, _co(), style)
        assert "20 years in finance" in ctx

    def test_no_context_returns_fallback(self, style):
        co = _co(industry="Other", employee_count=0)
        ctx = _build_context_block(_ct(), co, style)
        assert "No additional context" in ctx


# ---------------------------------------------------------------------------
# _validate_dm
# ---------------------------------------------------------------------------


class TestValidateDM:
    def test_valid_dm_passes(self):
        ok, _ = _validate_dm("Hi Jane, great to reconnect! Tony", "day1")
        assert ok is True

    @pytest.mark.parametrize("phrase", [
        "I hope this finds you well",
        "let's touch base soon",
        "we can leverage this",
        "great synergy between our teams",
    ])
    def test_blacklisted_phrases_rejected(self, phrase):
        ok, reason = _validate_dm(f"Hi Jane, {phrase}. Tony", "day1")
        assert ok is False
        assert "blacklisted" in reason.lower()

    def test_day7_over_300_chars_rejected(self):
        text = "A" * 301
        ok, reason = _validate_dm(text, "day7")
        assert ok is False
        assert "300" in reason

    def test_day14_over_300_chars_rejected(self):
        text = "B" * 350
        ok, reason = _validate_dm(text, "day14")
        assert ok is False

    def test_day1_reactivation_over_500_chars_allowed(self):
        """day1_reactivation limit is 500 chars."""
        text = "C" * 400
        ok, _ = _validate_dm(text, "day1")
        assert ok is True

    def test_unreplaced_template_variable(self):
        ok, reason = _validate_dm("Hi {name}, how are you?", "day1")
        assert ok is False
        assert "template" in reason.lower()

    def test_calendly_link_allowed(self):
        ok, _ = _validate_dm(
            "Let's chat! Book here: [CALENDLY_LINK]. Tony", "day21"
        )
        assert ok is True


# ---------------------------------------------------------------------------
# make_draft_entry
# ---------------------------------------------------------------------------


class TestMakeDraftEntry:
    def test_structure(self):
        entry = make_draft_entry("day1", "Hi Jane, great to connect!")
        assert entry["touch"] == "day1"
        assert entry["draft"] == "Hi Jane, great to connect!"
        assert entry["sent_at"] is None
        assert "generated_at" in entry

    def test_different_touches(self):
        for touch in ("day1", "day7", "day14", "day21"):
            entry = make_draft_entry(touch, "test")
            assert entry["touch"] == touch


# ---------------------------------------------------------------------------
# DMGenerator
# ---------------------------------------------------------------------------


class TestDMGenerator:
    def _mock_api_response(self, text: str) -> MagicMock:
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        return msg

    def test_generate_dm_re_activation_day1(self):
        gen = DMGenerator(anthropic_api_key="fake")
        with patch.object(gen._client.messages, "create",
                          return_value=self._mock_api_response("Hi Jane, great to reconnect! Tony")):
            result = gen.generate_dm(_ct(), _co(), "day1")
        assert "Jane" in result
        assert "Tony" in result

    def test_generate_dm_cold_new_day1(self):
        gen = DMGenerator(anthropic_api_key="fake")
        ct = _ct(flow_type="cold_new")
        with patch.object(gen._client.messages, "create",
                          return_value=self._mock_api_response("Hi Jane, I work with manufacturing firms on T&E. Tony")):
            result = gen.generate_dm(ct, _co(), "day1")
        assert len(result) > 0

    def test_generate_dm_day21_with_calendly(self):
        gen = DMGenerator(anthropic_api_key="fake")
        draft = "Jane, would you be open to a quick chat? [CALENDLY_LINK] Tony"
        with patch.object(gen._client.messages, "create",
                          return_value=self._mock_api_response(draft)):
            result = gen.generate_dm(_ct(), _co(), "day21")
        assert "[CALENDLY_LINK]" in result

    def test_retries_on_blacklisted_phrase(self):
        gen = DMGenerator(anthropic_api_key="fake")
        bad = "I hope this finds you well, Jane!"
        good = "Jane, quick thought on cross-border compliance. Tony"
        call_count = 0

        def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            text = bad if call_count == 1 else good
            return self._mock_api_response(text)

        with patch.object(gen._client.messages, "create", side_effect=mock_create):
            result = gen.generate_dm(_ct(), _co(), "day1")

        assert call_count == 2
        assert "hope this finds" not in result.lower()

    def test_retries_on_day7_too_long(self):
        gen = DMGenerator(anthropic_api_key="fake")
        long_text = "A" * 350
        short_text = "Short insight. Tony"
        call_count = 0

        def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            text = long_text if call_count == 1 else short_text
            return self._mock_api_response(text)

        with patch.object(gen._client.messages, "create", side_effect=mock_create):
            result = gen.generate_dm(_ct(), _co(), "day7")

        assert call_count == 2
        assert len(result) <= 300

    def test_returns_last_attempt_after_max_retries(self):
        gen = DMGenerator(anthropic_api_key="fake")
        bad = "Let's touch base and leverage synergy!"

        with patch.object(gen._client.messages, "create",
                          return_value=self._mock_api_response(bad)):
            result = gen.generate_dm(_ct(), _co(), "day1")

        assert result == bad

    def test_invalid_touch_raises(self):
        gen = DMGenerator(anthropic_api_key="fake")
        with pytest.raises(ValueError, match="Invalid touch"):
            gen.generate_dm(_ct(), _co(), "day99")

    def test_api_called_with_correct_params(self):
        gen = DMGenerator(anthropic_api_key="fake")
        mock_create = MagicMock(
            return_value=self._mock_api_response("Hi Jane. Tony")
        )
        with patch.object(gen._client.messages, "create", mock_create):
            gen.generate_dm(_ct(), _co(), "day1")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 200
        assert call_kwargs["temperature"] == 0.7
        assert "Tony" in call_kwargs["system"]
        assert "Hitpoint" in call_kwargs["system"]
        assert call_kwargs["messages"][0]["content"] == "Generate the DM now."

    def test_prompts_differ_by_flow_type(self):
        """Re-activation and cold_new produce different system prompts."""
        gen = DMGenerator(anthropic_api_key="fake")
        prompts: list[str] = []

        def capture_create(**kwargs):
            prompts.append(kwargs["system"])
            msg = MagicMock()
            msg.content = [MagicMock(text="Hi. Tony")]
            return msg

        with patch.object(gen._client.messages, "create", side_effect=capture_create):
            gen.generate_dm(_ct(flow_type="re_activation"), _co(), "day1")
            gen.generate_dm(_ct(flow_type="cold_new"), _co(), "day1")

        assert len(prompts) == 2
        # Re-activation mentions reconnection, cold mentions cold outreach
        assert "re-activation" in prompts[0].lower() or "1st-degree" in prompts[0]
        assert "cold" in prompts[1].lower()


# ---------------------------------------------------------------------------
# CLI: pipeline generate-dms
# ---------------------------------------------------------------------------


class TestGenerateDMsCLI:
    def test_help(self):
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["pipeline", "generate-dms", "--help"])
        assert result.exit_code == 0
        assert "day1" in result.output

    def test_invalid_touch(self):
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        with patch("src.cli.load_settings") as mock:
            s = MagicMock()
            s.anthropic_api_key = "fake"
            s.feishu_app_id = "x"
            s.feishu_app_secret = "x"
            s.feishu_bitable_app_token = "x"
            s.feishu_companies_table_id = "x"
            s.feishu_contacts_table_id = "x"
            mock.return_value = s
            result = runner.invoke(app, ["pipeline", "generate-dms", "--touch", "day99"])
        assert result.exit_code == 1

    def test_aborts_without_anthropic_key(self):
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        with patch("src.cli.load_settings") as mock:
            s = MagicMock()
            s.anthropic_api_key = ""
            mock.return_value = s
            result = runner.invoke(app, ["pipeline", "generate-dms", "--touch", "day1"])
        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output

    def test_aborts_without_feishu(self):
        from typer.testing import CliRunner
        from src.cli import app
        runner = CliRunner()
        with patch("src.cli.load_settings") as mock:
            s = MagicMock()
            s.anthropic_api_key = "fake"
            s.feishu_app_id = ""
            s.feishu_app_secret = ""
            s.feishu_bitable_app_token = ""
            s.feishu_companies_table_id = ""
            s.feishu_contacts_table_id = ""
            mock.return_value = s
            result = runner.invoke(app, ["pipeline", "generate-dms", "--touch", "day1"])
        assert result.exit_code == 1
