"""Daily action report and pipeline statistics generation."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Sequence

from src.models import Company, Contact


# ---------------------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------------------


def generate_daily_report(
    contacts: Sequence[Contact],
    companies: Sequence[Company],
    target_date: date | None = None,
) -> dict:
    """Build a daily action report for the given date (default: today).

    Returns a dict with all data needed for the CLI to render a rich report.
    """
    today = target_date or date.today()

    # ---- Actions due today ----
    due_today: list[Contact] = []
    for ct in contacts:
        if ct.next_touch_date and ct.next_touch_date.date() <= today:
            due_today.append(ct)
    due_today.sort(key=lambda c: c.lead_score, reverse=True)

    # ---- Queued (ready to send) ----
    queued: list[Contact] = [
        ct for ct in contacts if ct.dm_status.endswith("_queued")
    ]

    # ---- Pending acceptances (cold_new day1_sent) ----
    pending_acceptances: list[Contact] = [
        ct for ct in contacts
        if ct.dm_status == "day1_sent" and ct.flow_type == "cold_new"
    ]

    # ---- Sent today ----
    sent_today: list[Contact] = [
        ct for ct in contacts
        if ct.last_touch_date and ct.last_touch_date.date() == today
    ]

    # ---- Need DM generation (not_started with lead_score > 0) ----
    need_drafts: list[Contact] = [
        ct for ct in contacts
        if ct.dm_status == "not_started" and ct.lead_score > 0
    ]
    need_drafts.sort(key=lambda c: c.lead_score, reverse=True)

    # ---- Replies requiring attention ----
    replied: list[Contact] = [
        ct for ct in contacts if ct.dm_status == "replied"
    ]

    # ---- Meetings booked ----
    meetings: list[Contact] = [
        ct for ct in contacts if ct.dm_status == "meeting_booked"
    ]

    # ---- Suggested daily workflow ----
    steps: list[dict] = []

    if pending_acceptances:
        steps.append({
            "command": "pipeline check-acceptances",
            "description": f"Check {len(pending_acceptances)} pending connection requests",
            "priority": 1,
        })

    if replied:
        steps.append({
            "command": "(manual)",
            "description": f"Reply to {len(replied)} contacts who responded",
            "priority": 2,
        })

    if need_drafts:
        top5 = need_drafts[:5]
        names = ", ".join(ct.name for ct in top5)
        steps.append({
            "command": "pipeline generate-dms --touch day1",
            "description": f"Generate DMs for {len(need_drafts)} contacts ({names}...)",
            "priority": 3,
        })

    if queued:
        by_touch = Counter(ct.dm_status.replace("_queued", "") for ct in queued)
        touch_summary = ", ".join(f"{t}: {n}" for t, n in sorted(by_touch.items()))
        steps.append({
            "command": "pipeline warm --touch <touch>",
            "description": f"Warm {len(queued)} queued contacts ({touch_summary})",
            "priority": 4,
        })
        steps.append({
            "command": "pipeline push-dms --touch <touch>",
            "description": f"Send {len(queued)} queued DMs",
            "priority": 5,
        })

    if due_today and not queued:
        # Due contacts that need DM generation first
        by_touch: dict[str, int] = {}
        for ct in due_today:
            status = ct.dm_status
            if status.endswith("_sent"):
                next_touch = {
                    "day1_sent": "day7",
                    "day7_sent": "day14",
                    "day14_sent": "day21",
                }.get(status, "?")
                by_touch[next_touch] = by_touch.get(next_touch, 0) + 1
        if by_touch:
            touch_summary = ", ".join(f"{t}: {n}" for t, n in sorted(by_touch.items()))
            steps.append({
                "command": "pipeline generate-dms --touch <touch>",
                "description": f"Generate follow-up DMs for {sum(by_touch.values())} due contacts ({touch_summary})",
                "priority": 3,
            })

    steps.sort(key=lambda s: s["priority"])

    return {
        "date": today,
        "due_today": due_today,
        "queued": queued,
        "pending_acceptances": pending_acceptances,
        "sent_today": sent_today,
        "need_drafts": need_drafts,
        "replied": replied,
        "meetings": meetings,
        "steps": steps,
        "total_contacts": len(contacts),
        "total_companies": len(companies),
    }


# ---------------------------------------------------------------------------
# Weekly planner
# ---------------------------------------------------------------------------


def generate_weekly_plan(
    contacts: Sequence[Contact],
    start_date: date | None = None,
) -> list[dict]:
    """Build a 7-day forward view of scheduled touches.

    Returns a list of {date, contacts_due, by_touch} for each day.
    """
    start = start_date or date.today()
    plan: list[dict] = []

    for offset in range(7):
        day = start + timedelta(days=offset)
        due: list[Contact] = []
        for ct in contacts:
            if ct.next_touch_date and ct.next_touch_date.date() == day:
                due.append(ct)
        due.sort(key=lambda c: c.lead_score, reverse=True)

        by_touch = Counter()
        for ct in due:
            status = ct.dm_status
            if status.endswith("_sent"):
                next_touch = {
                    "day1_sent": "day7",
                    "day7_sent": "day14",
                    "day14_sent": "day21",
                }.get(status, "follow-up")
                by_touch[next_touch] += 1
            elif status.endswith("_queued"):
                by_touch[status.replace("_queued", "")] += 1
            else:
                by_touch["other"] += 1

        plan.append({
            "date": day,
            "weekday": day.strftime("%a"),
            "contacts_due": due,
            "count": len(due),
            "by_touch": dict(by_touch),
        })

    return plan


# ---------------------------------------------------------------------------
# Pipeline statistics
# ---------------------------------------------------------------------------


def generate_pipeline_stats(
    contacts: Sequence[Contact],
    companies: Sequence[Company],
) -> dict:
    """Aggregate pipeline statistics for the stats command."""
    # Contact status distribution
    status_dist = Counter(ct.dm_status for ct in contacts)

    # Flow type distribution
    flow_dist = Counter(ct.flow_type for ct in contacts)

    # Contact type distribution
    type_dist = Counter(ct.contact_type for ct in contacts)

    # Company outreach status
    outreach_dist = Counter(co.outreach_status for co in companies)

    # Company industry distribution
    industry_dist = Counter(co.industry for co in companies)

    # Company source distribution
    source_dist = Counter(co.source for co in companies)

    # Score distributions
    co_scores = [co.lead_score for co in companies]
    ct_scores = [ct.lead_score for ct in contacts]

    # Funnel metrics
    total_contacts = len(contacts)
    touched = sum(1 for ct in contacts if ct.touch_count > 0)
    replied = sum(1 for ct in contacts if ct.dm_status == "replied")
    meetings = sum(1 for ct in contacts if ct.dm_status == "meeting_booked")
    rejected = sum(1 for ct in contacts if ct.dm_status == "rejected")

    reply_rate = (replied / touched * 100) if touched else 0
    meeting_rate = (meetings / touched * 100) if touched else 0

    # SAP / Concur coverage
    sap_yes = sum(1 for co in companies if co.sap_user == "Yes")
    concur_yes = sum(1 for co in companies if co.uses_concur == "Yes")
    enriched = sum(1 for co in companies if co.enrichment_signals)

    return {
        "total_contacts": total_contacts,
        "total_companies": len(companies),
        "status_dist": dict(status_dist),
        "flow_dist": dict(flow_dist),
        "type_dist": dict(type_dist),
        "outreach_dist": dict(outreach_dist),
        "industry_dist": dict(industry_dist),
        "source_dist": dict(source_dist),
        "avg_company_score": sum(co_scores) // len(co_scores) if co_scores else 0,
        "avg_contact_score": sum(ct_scores) // len(ct_scores) if ct_scores else 0,
        "max_company_score": max(co_scores) if co_scores else 0,
        "max_contact_score": max(ct_scores) if ct_scores else 0,
        # Funnel
        "touched": touched,
        "replied": replied,
        "meetings": meetings,
        "rejected": rejected,
        "reply_rate": reply_rate,
        "meeting_rate": meeting_rate,
        # Coverage
        "sap_confirmed": sap_yes,
        "concur_confirmed": concur_yes,
        "enriched_companies": enriched,
    }
