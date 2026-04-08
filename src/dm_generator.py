"""DM generation via Anthropic Claude API with quality validation."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import anthropic

from src.models import Company, Contact

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

# Words/phrases that produce generic, low-quality DMs
_BLACKLIST_PHRASES = [
    "i hope this finds you well",
    "i hope this message finds you",
    "leverage",
    "synergy",
    "touch base",
    "circle back",
    "low-hanging fruit",
    "paradigm",
    "deep dive",
]

# Industry → pain-point mapping for prompt context
_INDUSTRY_PAIN_POINTS: dict[str, str] = {
    "Manufacturing": (
        "cross-border expense compliance, per diem management by country, "
        "multi-currency T&E reconciliation"
    ),
    "Mining": (
        "remote workforce T&E, FIFO expense tracking, "
        "site-based approvals for high-cost logistics"
    ),
    "Education": (
        "FBT (Fringe Benefits Tax) obligations, salary packaging compliance, "
        "research grant expense acquittal"
    ),
    "Professional Services": (
        "client-billable expense recovery, multi-entity consolidation, "
        "real-time project cost visibility"
    ),
}

# --------------------------------------------------------------------------
# System prompts
# --------------------------------------------------------------------------

_SHARED_CONTEXT = """\
You are ghostwriting a LinkedIn DM on behalf of Tony, CEO of Hitpoint Solution.
Hitpoint is an SAP Concur implementation and optimisation partner covering \
Australia, New Zealand, and Greater China.

Rules:
- Australian English spelling (organisation, optimisation, colour).
- Maximum 4 sentences per DM.
- Be conversational, not corporate. No buzzwords.
- Never start with "I hope this finds you well" or similar generic openers.
- Never use the words: leverage, synergy, touch base, circle back.
- Do NOT reveal that you researched the recipient (no "I noticed…", "I saw that…").
- Weave context naturally as if Tony already knows the industry well.
- Sign off as "Tony" (no surname, no title).
"""

_RE_ACTIVATION_PROMPTS: dict[str, str] = {
    "day1": """\
{shared}

TASK: Write a Day 1 re-activation DM to {name} ({title} at {company}).
Tony and {name} are already 1st-degree LinkedIn connections.
Strategy: Warm reconnection. Reference something specific and genuine \
(their role, company, or industry), then ask a soft, open-ended question. \
No selling. No Concur mention. Just re-establish the relationship.

{context}
""",
    "day7": """\
{shared}

TASK: Write a Day 7 follow-up to {name} ({title} at {company}).
Strategy: Share a useful industry insight or observation. No pitch. \
Keep under 300 characters. Position Tony as someone who thinks about \
{industry} challenges.

{context}
""",
    "day14": """\
{shared}

TASK: Write a Day 14 follow-up to {name} ({title} at {company}).
Strategy: Offer something concrete and free — a Concur Health Check, \
an FBT compliance guide, a benchmark report, or a quick policy review. \
Mention it as something Tony does for peers, not as a sales offer. \
Keep under 300 characters.

{context}
""",
    "day21": """\
{shared}

TASK: Write a Day 21 final follow-up to {name} ({title} at {company}).
Strategy: Direct but respectful ask for a 15-minute call. Include \
[CALENDLY_LINK] as the booking link placeholder. Keep it short. \
Acknowledge they're busy.

{context}
""",
}

_COLD_NEW_PROMPTS: dict[str, str] = {
    "day1": """\
{shared}

TASK: Write a Day 1 connection-request note to {name} ({title} at {company}).
Tony does NOT know {name}. This accompanies a LinkedIn connection request.
Strategy: Ultra-short (2-3 sentences max). Lead with an industry-specific \
pain point, not with Tony's credentials. End with why connecting makes sense.
Must be under 300 characters (LinkedIn note limit).

{context}
""",
    "day7": """\
{shared}

TASK: Write a Day 7 follow-up to {name} ({title} at {company}).
The connection request was accepted. This is the first real message.
Strategy: Share a relevant compliance change, industry stat, or \
practical tip. No selling. Keep under 300 characters.

{context}
""",
    "day14": """\
{shared}

TASK: Write a Day 14 follow-up to {name} ({title} at {company}).
Strategy: Deliver specific free value — a benchmark, template, or \
guide relevant to their role. Keep under 300 characters.

{context}
""",
    "day21": """\
{shared}

TASK: Write a Day 21 final follow-up to {name} ({title} at {company}).
Strategy: Last touch. Brief, direct. Offer a 15-minute call with \
[CALENDLY_LINK]. No pressure, no guilt. 2-3 sentences max.

{context}
""",
}


# --------------------------------------------------------------------------
# Context builder
# --------------------------------------------------------------------------


def _build_context_block(contact: Contact, company: Company) -> str:
    """Build the {context} section for the prompt."""
    parts: list[str] = []

    # Industry pain points
    pain = _INDUSTRY_PAIN_POINTS.get(company.industry)
    if pain:
        parts.append(f"Industry pain points for {company.industry}: {pain}")

    # Concur / SAP angle
    if company.uses_concur == "Yes":
        parts.append(
            "Angle: They already use SAP Concur — position Tony as an "
            "optimisation/health-check expert, not a new vendor."
        )
    elif company.sap_user == "Yes":
        parts.append(
            "Angle: They use SAP but not Concur — position Concur as "
            "a natural extension of their SAP investment."
        )

    # Overseas / expansion
    if company.has_overseas_offices:
        parts.append(
            "They have overseas offices — cross-border T&E compliance "
            "and multi-entity rollout are relevant angles."
        )

    # News
    news = company.enrichment_signals.get("recent_news", [])
    if news:
        titles = [n.get("title", "") for n in news[:2]]
        parts.append(
            f"Recent company news (weave naturally, don't say 'I saw'): "
            f"{'; '.join(titles)}"
        )

    # Expansion / M&A
    for n in news:
        text = (n.get("title", "") + " " + n.get("description", "")).lower()
        if "expansion" in text or "acquisition" in text or "merger" in text:
            parts.append(
                "M&A or expansion detected — angle: scaling T&E processes "
                "to new entities and geographies."
            )
            break

    # Employee count
    if company.employee_count:
        parts.append(f"Company size: ~{company.employee_count} employees.")

    # Contact profile
    if contact.profile_summary:
        parts.append(f"Contact bio: {contact.profile_summary[:200]}")

    return "\n".join(parts) if parts else "No additional context available."


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _validate_dm(text: str, touch: str) -> tuple[bool, str]:
    """Validate DM quality. Returns (ok, reason)."""
    # Blacklist check
    lower = text.lower()
    for phrase in _BLACKLIST_PHRASES:
        if phrase in lower:
            return False, f"Contains blacklisted phrase: '{phrase}'"

    # Length check for short touches
    if touch in ("day7", "day14") and len(text) > 300:
        return False, f"Too long for {touch}: {len(text)} chars (max 300)"

    # Day 1 cold_new connection note also has 300 char limit
    # (handled in prompt but double-check)

    # Unreplaced template variables
    if "{" in text and "}" in text:
        # Allow [CALENDLY_LINK] which uses brackets
        stripped = text.replace("[CALENDLY_LINK]", "")
        if re.search(r"\{[^}]+\}", stripped):
            return False, "Contains unreplaced template variable"

    return True, ""


# --------------------------------------------------------------------------
# DMGenerator
# --------------------------------------------------------------------------


class DMGenerator:
    """Generate personalised LinkedIn DMs using Claude API."""

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model

    def generate_dm(
        self,
        contact: Contact,
        company: Company,
        touch: str,
    ) -> str:
        """Generate a DM for the given touch point.

        Args:
            contact: The target contact.
            company: The contact's company.
            touch: One of "day1", "day7", "day14", "day21".

        Returns:
            The generated DM text.
        """
        prompt_map = (
            _RE_ACTIVATION_PROMPTS
            if contact.flow_type == "re_activation"
            else _COLD_NEW_PROMPTS
        )

        if touch not in prompt_map:
            raise ValueError(f"Invalid touch: {touch!r}")

        context_block = _build_context_block(contact, company)
        system_prompt = prompt_map[touch].format(
            shared=_SHARED_CONTEXT,
            name=contact.name.split()[0] if contact.name else "there",
            title=contact.title,
            company=company.company_name,
            industry=company.industry,
            context=context_block,
        )

        for attempt in range(1, MAX_RETRIES + 2):  # 1 initial + MAX_RETRIES
            raw = self._call_api(system_prompt)
            dm_text = raw.strip().strip('"').strip("'").strip()

            ok, reason = _validate_dm(dm_text, touch)
            if ok:
                return dm_text

            logger.warning(
                "DM validation failed (attempt %d/%d): %s — regenerating",
                attempt, MAX_RETRIES + 1, reason,
            )

        # Return last attempt even if imperfect
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
