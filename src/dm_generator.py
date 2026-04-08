"""DM generation via Anthropic Claude API with YAML-driven style configuration."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from src.models import Company, Contact

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
_STYLE_PATH = Path(__file__).parent / "dm_style.yaml"


# --------------------------------------------------------------------------
# Style loading
# --------------------------------------------------------------------------


def _load_style(path: Path | None = None) -> dict:
    """Load dm_style.yaml. Returns parsed dict."""
    import yaml  # lazy import — only needed here

    p = path or _STYLE_PATH
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_style() -> dict:
    """Cached style loader — reloads on file change."""
    mtime = _STYLE_PATH.stat().st_mtime if _STYLE_PATH.exists() else 0
    if not hasattr(_get_style, "_cache") or _get_style._mtime != mtime:
        _get_style._cache = _load_style()
        _get_style._mtime = mtime
    return _get_style._cache


# --------------------------------------------------------------------------
# Prompt builder (from YAML)
# --------------------------------------------------------------------------


def _build_shared_prompt(style: dict) -> str:
    """Build the shared system prompt from style config."""
    p = style["persona"]
    v = style["voice"]

    rules = [
        f"{v['language']}.",
        f"Tone: {v['tone']}",
        f"Maximum {v['max_sentences']} sentences per DM.",
        f"Sign off as \"{v['sign_off']}\" — no surname, no title.",
    ]
    for phrase in v.get("blacklist", []):
        rules.append(f"Never use: \"{phrase}\".")
    for avoid in v.get("avoid", []):
        rules.append(avoid)
    for prefer in v.get("prefer", []):
        rules.append(prefer)

    rules_block = "\n".join(f"- {r}" for r in rules)

    return (
        f"You are ghostwriting a LinkedIn DM on behalf of {p['name']}, "
        f"{p['role']} of {p['company']}.\n"
        f"{p['company']} is a {p['product']} partner covering {p['coverage']}.\n\n"
        f"Rules:\n{rules_block}\n"
    )


def _build_task_prompt(
    style: dict,
    contact: Contact,
    company: Company,
    touch: str,
) -> str:
    """Build the task-specific part of the prompt from style config."""
    flow = contact.flow_type
    first_name = contact.name.split()[0] if contact.name else "there"

    strategy = style["touch_strategy"].get(flow, {}).get(touch, "")
    limits = style.get("limits", {})

    # Determine char limit for this touch
    limit_key = f"{touch}_{flow}" if f"{touch}_{flow}" in limits else touch
    if touch == "day1" and flow == "cold_new":
        limit_key = "day1_cold"
    elif touch == "day1" and flow == "re_activation":
        limit_key = "day1_reactivation"
    char_limit = limits.get(limit_key, 500)

    task = (
        f"TASK: Write a {touch.replace('day', 'Day ')} "
        f"{'re-activation' if flow == 're_activation' else 'cold outreach'} DM "
        f"to {first_name} ({contact.title} at {company.company_name}).\n"
    )
    if flow == "re_activation":
        task += f"{contact.name.split()[0]} and Tony are already 1st-degree connections.\n"
    if strategy:
        task += f"Strategy: {strategy}\n"
    if char_limit <= 300:
        task += f"HARD LIMIT: Keep under {char_limit} characters.\n"

    return task


def _build_context_block(contact: Contact, company: Company, style: dict) -> str:
    """Build enrichment context from company signals + style config."""
    parts: list[str] = []

    # Industry angles from YAML
    ind_cfg = style.get("industry_angles", {}).get(company.industry, {})
    if ind_cfg:
        parts.append(
            f"Industry ({company.industry}): {ind_cfg.get('pain_points', '')}. "
            f"Angle: {ind_cfg.get('angle', '')}."
        )

    # Signal angles from YAML
    signal_angles = style.get("signal_angles", {})
    if company.uses_concur == "Yes" and "uses_concur_yes" in signal_angles:
        parts.append(f"Angle: {signal_angles['uses_concur_yes']}")
    elif company.sap_user == "Yes" and "sap_no_concur" in signal_angles:
        parts.append(f"Angle: {signal_angles['sap_no_concur']}")

    if company.has_overseas_offices and "overseas_offices" in signal_angles:
        parts.append(f"Angle: {signal_angles['overseas_offices']}")

    news = company.enrichment_signals.get("recent_news", [])
    if news:
        titles = [n.get("title", "") for n in news[:2]]
        parts.append(f"Recent news (weave naturally, don't say 'I saw'): {'; '.join(titles)}")
        for n in news:
            text = (n.get("title", "") + " " + n.get("description", "")).lower()
            if "expansion" in text or "acquisition" in text or "merger" in text:
                if "expansion_news" in signal_angles:
                    parts.append(f"Angle: {signal_angles['expansion_news']}")
                break

    if company.enrichment_signals.get("concur_job_postings") and "concur_job_postings" in signal_angles:
        parts.append(f"Angle: {signal_angles['concur_job_postings']}")

    if company.employee_count:
        parts.append(f"Company size: ~{company.employee_count} employees.")

    if contact.profile_summary:
        parts.append(f"Contact bio: {contact.profile_summary[:200]}")

    return "\n".join(parts) if parts else "No additional context available."


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _validate_dm(text: str, touch: str, style: dict | None = None) -> tuple[bool, str]:
    """Validate DM quality against style rules. Returns (ok, reason)."""
    s = style or _get_style()
    blacklist = [p.lower() for p in s.get("voice", {}).get("blacklist", [])]

    lower = text.lower()
    for phrase in blacklist:
        if phrase in lower:
            return False, f"Contains blacklisted phrase: '{phrase}'"

    # Length limits from YAML
    # Keys like "day7: 300" apply to all flows for that touch.
    # Keys like "day1_cold: 300" / "day1_reactivation: 500" are flow-specific.
    # For validation without knowing flow, use the most generous limit available.
    limits = s.get("limits", {})
    candidates = [
        limits[k] for k in (touch, f"{touch}_cold", f"{touch}_reactivation")
        if k in limits
    ]
    if candidates:
        max_limit = max(candidates)  # most generous limit for this touch
        if len(text) > max_limit:
            return False, f"Too long for {touch}: {len(text)} chars (max {max_limit})"

    # Unreplaced template variables
    if "{" in text and "}" in text:
        stripped = text.replace("[CALENDLY_LINK]", "")
        if re.search(r"\{[^}]+\}", stripped):
            return False, "Contains unreplaced template variable"

    return True, ""


# --------------------------------------------------------------------------
# DMGenerator
# --------------------------------------------------------------------------


class DMGenerator:
    """Generate personalised LinkedIn DMs using Claude API + YAML style config."""

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-20250514",
        style_path: Path | None = None,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model
        self._style = _load_style(style_path) if style_path else _get_style()

    def generate_dm(
        self,
        contact: Contact,
        company: Company,
        touch: str,
    ) -> str:
        """Generate a DM for the given touch point."""
        flow = contact.flow_type
        if touch not in self._style.get("touch_strategy", {}).get(flow, {}):
            raise ValueError(f"Invalid touch: {touch!r} for flow {flow!r}")

        shared = _build_shared_prompt(self._style)
        task = _build_task_prompt(self._style, contact, company, touch)
        context = _build_context_block(contact, company, self._style)
        system_prompt = f"{shared}\n{task}\n{context}"

        for attempt in range(1, MAX_RETRIES + 2):
            raw = self._call_api(system_prompt)
            dm_text = raw.strip().strip('"').strip("'").strip()

            ok, reason = _validate_dm(dm_text, touch, self._style)
            if ok:
                return dm_text

            logger.warning(
                "DM validation failed (attempt %d/%d): %s — regenerating",
                attempt, MAX_RETRIES + 1, reason,
            )

        logger.error("DM validation failed after all retries, returning last attempt")
        return dm_text

    def _call_api(self, system_prompt: str) -> str:
        """Call the Anthropic API and return the text response."""
        message = self._client.messages.create(
            model=self.model,
            max_tokens=200,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": "Generate the DM now."}],
        )
        return message.content[0].text


def make_draft_entry(touch: str, draft: str) -> dict:
    """Build a dm_drafts list entry."""
    return {
        "touch": touch,
        "draft": draft,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": None,
    }
