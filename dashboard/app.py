"""LinkedIn ABM Agent — Streamlit Dashboard."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.daily_actions import generate_daily_report, generate_pipeline_stats, generate_weekly_plan
from src.lead_scorer import score_company, score_contact, get_company_breakdown, get_contact_breakdown
from src.models import Company, Contact

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="LinkedIn ABM Agent",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — clean, minimal design inspired by Claude Code Desktop
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    /* Tighter spacing */
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    /* Metric cards */
    [data-testid="stMetric"] {
        background: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetric"] label { font-size: 0.8rem; color: #6c757d; }
    /* Sidebar */
    [data-testid="stSidebar"] { background: #1a1a2e; }
    [data-testid="stSidebar"] * { color: #e0e0e0 !important; }
    /* Tables */
    .stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def load_data() -> tuple[list[Company], list[Contact], str]:
    """Try Bitable first, fall back to demo data."""
    try:
        from src.config import get_settings
        settings = get_settings()
        if all([settings.feishu_app_id, settings.feishu_app_secret,
                settings.feishu_bitable_app_token]):
            from src.bitable_client import BitableClient
            bt = BitableClient.from_settings(settings)
            raw_co = bt.list_records(bt.companies_table_id)
            raw_ct = bt.list_records(bt.contacts_table_id)
            companies = [bt._fields_to_company(r["fields"]) for r in raw_co if "fields" in r]
            contacts = [bt._fields_to_contact(r["fields"]) for r in raw_ct if "fields" in r]
            return companies, contacts, "live"
    except Exception:
        pass
    from dashboard.demo_data import generate_demo_companies, generate_demo_contacts
    companies = generate_demo_companies()
    contacts = generate_demo_contacts(companies)
    return companies, contacts, "demo"


companies, contacts, data_source = load_data()
companies_map = {c.company_name: c for c in companies}

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🎯 LinkedIn ABM Agent")
    if data_source == "demo":
        st.caption("📊 Demo data — connect Feishu Bitable for live data")
    else:
        st.caption("🟢 Live — connected to Bitable")
    st.divider()
    page = st.radio("Navigation", [
        "📊 Overview",
        "🏢 Companies",
        "👤 Contacts",
        "✉️ DM Drafts",
        "📈 Pipeline",
        "⚙️ Settings",
    ], label_visibility="collapsed")

# ---------------------------------------------------------------------------
# Page: Overview
# ---------------------------------------------------------------------------

if page == "📊 Overview":
    st.title("Pipeline Dashboard")

    report = generate_daily_report(contacts, companies)
    stats = generate_pipeline_stats(contacts, companies)

    # Top metrics row
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Companies", len(companies))
    c2.metric("Contacts", len(contacts))
    c3.metric("Touched", stats["touched"])
    c4.metric("Replied", stats["replied"])
    c5.metric("Meetings", stats["meetings"])
    c6.metric("Reply Rate", f"{stats['reply_rate']:.1f}%")

    st.divider()

    # Two column layout
    left, right = st.columns([3, 2])

    with left:
        # Funnel chart
        st.subheader("Pipeline Funnel")
        funnel_data = {
            "Stage": ["Companies", "Enriched", "Contacts", "Touched", "Replied", "Meetings"],
            "Count": [
                len(companies), stats["enriched_companies"],
                len(contacts), stats["touched"],
                stats["replied"], stats["meetings"],
            ],
        }
        fig = go.Figure(go.Funnel(
            y=funnel_data["Stage"],
            x=funnel_data["Count"],
            textinfo="value+percent initial",
            marker=dict(color=["#4361ee", "#4895ef", "#4cc9f0", "#f72585", "#b5179e", "#7209b7"]),
        ))
        fig.update_layout(height=350, margin=dict(l=0, r=0, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with right:
        # Today's actions
        st.subheader("Today's Actions")
        due = report["due_today"]
        queued = report["queued"]
        pending = report["pending_acceptances"]
        need = report["need_drafts"]

        if pending:
            st.warning(f"🔔 {len(pending)} pending connection requests — run `check-acceptances`")
        if report["replied"]:
            st.success(f"🎉 {len(report['replied'])} replies need attention!")
        if queued:
            st.info(f"📨 {len(queued)} DMs queued and ready to send")
        if need:
            st.info(f"✏️ {len(need)} contacts need DM drafts")
        if due:
            st.info(f"📅 {len(due)} contacts due for follow-up today")
        if not any([pending, report["replied"], queued, need, due]):
            st.success("✅ All caught up! No actions needed today.")

        # Suggested workflow
        if report["steps"]:
            st.subheader("Suggested Workflow")
            for i, step in enumerate(report["steps"], 1):
                st.markdown(f"**{i}.** `{step['command']}`")
                st.caption(step["description"])

    # Weekly plan
    st.divider()
    st.subheader("7-Day Forward Plan")
    plan = generate_weekly_plan(contacts)
    plan_cols = st.columns(7)
    today = date.today()
    for i, day_plan in enumerate(plan):
        with plan_cols[i]:
            is_today = day_plan["date"] == today
            label = f"**{day_plan['weekday']}**" if not is_today else f"**🔵 {day_plan['weekday']}**"
            st.markdown(label)
            st.metric(day_plan["date"].strftime("%m/%d"), day_plan["count"], label_visibility="visible")
            if day_plan["by_touch"]:
                for t, n in sorted(day_plan["by_touch"].items()):
                    st.caption(f"{t}: {n}")

# ---------------------------------------------------------------------------
# Page: Companies
# ---------------------------------------------------------------------------

elif page == "🏢 Companies":
    st.title("Companies")

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        ind_filter = st.multiselect("Industry", sorted(set(c.industry for c in companies)))
    with fc2:
        sap_filter = st.selectbox("SAP User", ["All", "Yes", "No", "Unknown"])
    with fc3:
        min_score = st.slider("Min Lead Score", 0, 100, 0)

    filtered = companies
    if ind_filter:
        filtered = [c for c in filtered if c.industry in ind_filter]
    if sap_filter != "All":
        filtered = [c for c in filtered if c.sap_user == sap_filter]
    filtered = [c for c in filtered if c.lead_score >= min_score]
    filtered.sort(key=lambda c: c.lead_score, reverse=True)

    st.caption(f"Showing {len(filtered)} of {len(companies)} companies")

    # Score distribution
    col_chart, col_industry = st.columns(2)
    with col_chart:
        scores = [c.lead_score for c in filtered]
        if scores:
            fig = px.histogram(x=scores, nbins=10, labels={"x": "Lead Score", "y": "Count"},
                               title="Score Distribution")
            fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    with col_industry:
        ind_counts = Counter(c.industry for c in filtered)
        if ind_counts:
            fig = px.pie(names=list(ind_counts.keys()), values=list(ind_counts.values()),
                         title="By Industry")
            fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)

    # Table
    table_data = []
    for c in filtered:
        breakdown = get_company_breakdown(c)
        bd_str = "; ".join(f"{k} +{v}" for k, v in breakdown.items()) if breakdown else "—"
        table_data.append({
            "Company": c.company_name,
            "Industry": c.industry,
            "Employees": c.employee_count,
            "City": c.hq_city or "—",
            "SAP": c.sap_user,
            "Concur": c.uses_concur,
            "Overseas": "✅" if c.has_overseas_offices else "",
            "Status": c.outreach_status,
            "Score": c.lead_score,
            "Breakdown": bd_str,
        })
    st.dataframe(table_data, use_container_width=True, height=500)

# ---------------------------------------------------------------------------
# Page: Contacts
# ---------------------------------------------------------------------------

elif page == "👤 Contacts":
    st.title("Contacts")

    # Filters
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        flow_filter = st.selectbox("Flow Type", ["All", "re_activation", "cold_new"])
    with fc2:
        status_filter = st.selectbox("DM Status", ["All"] + sorted(set(c.dm_status for c in contacts)))
    with fc3:
        type_filter = st.selectbox("Contact Type", ["All"] + sorted(set(c.contact_type for c in contacts)))
    with fc4:
        min_ct_score = st.slider("Min Score", 0, 100, 0, key="ct_score")

    filtered_ct = contacts
    if flow_filter != "All":
        filtered_ct = [c for c in filtered_ct if c.flow_type == flow_filter]
    if status_filter != "All":
        filtered_ct = [c for c in filtered_ct if c.dm_status == status_filter]
    if type_filter != "All":
        filtered_ct = [c for c in filtered_ct if c.contact_type == type_filter]
    filtered_ct = [c for c in filtered_ct if c.lead_score >= min_ct_score]
    filtered_ct.sort(key=lambda c: c.lead_score, reverse=True)

    st.caption(f"Showing {len(filtered_ct)} of {len(contacts)} contacts")

    # Status distribution
    col1, col2 = st.columns(2)
    with col1:
        status_counts = Counter(c.dm_status for c in filtered_ct)
        if status_counts:
            fig = px.bar(x=list(status_counts.keys()), y=list(status_counts.values()),
                         labels={"x": "Status", "y": "Count"}, title="DM Status Distribution")
            fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        flow_counts = Counter(c.flow_type for c in filtered_ct)
        type_counts = Counter(c.contact_type for c in filtered_ct)
        fig = px.bar(x=list(type_counts.keys()), y=list(type_counts.values()),
                     labels={"x": "Type", "y": "Count"}, title="By Contact Type")
        fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Table
    table_data = []
    for ct in filtered_ct:
        comp = companies_map.get(ct.company_name)
        table_data.append({
            "Name": ct.name,
            "Title": ct.title,
            "Company": ct.company_name,
            "Type": ct.contact_type,
            "Flow": ct.flow_type,
            "Status": ct.dm_status,
            "Score": ct.lead_score,
            "Touches": ct.touch_count,
            "Last Touch": ct.last_touch_date.strftime("%Y-%m-%d") if ct.last_touch_date else "—",
            "Next Touch": ct.next_touch_date.strftime("%Y-%m-%d") if ct.next_touch_date else "—",
            "Provider ID": "✅" if ct.linkedin_provider_id else "—",
        })
    st.dataframe(table_data, use_container_width=True, height=500)

# ---------------------------------------------------------------------------
# Page: DM Drafts
# ---------------------------------------------------------------------------

elif page == "✉️ DM Drafts":
    st.title("DM Drafts")

    # Filter to contacts with drafts
    with_drafts = [ct for ct in contacts if ct.dm_drafts]
    queued = [ct for ct in contacts if ct.dm_status.endswith("_queued")]

    c1, c2, c3 = st.columns(3)
    c1.metric("Contacts with Drafts", len(with_drafts))
    c2.metric("Queued to Send", len(queued))
    c3.metric("Total Drafts", sum(len(ct.dm_drafts) for ct in with_drafts))

    st.divider()

    # Show each draft as a card
    view_filter = st.selectbox("Show", ["Queued (ready to send)", "All with drafts"])
    display = queued if view_filter.startswith("Queued") else with_drafts
    display.sort(key=lambda c: c.lead_score, reverse=True)

    if not display:
        st.info("No DM drafts to show. Run `pipeline generate-dms` to create some.")
    else:
        for ct in display[:30]:
            comp = companies_map.get(ct.company_name)
            comp_info = f"{comp.industry}, {comp.employee_count:,} emp" if comp else ""
            with st.expander(
                f"**{ct.name}** — {ct.contact_type} @ {ct.company_name} "
                f"| Score: {ct.lead_score} | {ct.flow_type} | {ct.dm_status}",
                expanded=ct.dm_status.endswith("_queued"),
            ):
                if comp_info:
                    st.caption(comp_info)
                for draft in ct.dm_drafts:
                    touch = draft.get("touch", "?")
                    sent = draft.get("sent_at")
                    status_icon = "✅" if sent else "📝"
                    st.markdown(f"**{status_icon} {touch.replace('day', 'Day ')}**")
                    st.text(draft.get("draft", ""))
                    if sent:
                        st.caption(f"Sent: {sent}")
                    st.markdown("---")

# ---------------------------------------------------------------------------
# Page: Pipeline Analytics
# ---------------------------------------------------------------------------

elif page == "📈 Pipeline":
    st.title("Pipeline Analytics")

    stats = generate_pipeline_stats(contacts, companies)

    # Conversion metrics
    st.subheader("Conversion Funnel")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Reply Rate", f"{stats['reply_rate']:.1f}%")
    c2.metric("Meeting Rate", f"{stats['meeting_rate']:.1f}%")
    c3.metric("SAP Confirmed", stats["sap_confirmed"])
    c4.metric("Concur Confirmed", stats["concur_confirmed"])

    # Flow comparison
    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("By Flow Type")
        for flow in ["re_activation", "cold_new"]:
            flow_contacts = [ct for ct in contacts if ct.flow_type == flow]
            touched = sum(1 for ct in flow_contacts if ct.touch_count > 0)
            replied = sum(1 for ct in flow_contacts if ct.dm_status == "replied")
            meetings = sum(1 for ct in flow_contacts if ct.dm_status == "meeting_booked")
            rate = (replied / touched * 100) if touched else 0
            label = "🟢 Re-activation" if flow == "re_activation" else "🔵 Cold New"
            st.markdown(f"**{label}**: {len(flow_contacts)} contacts, "
                        f"{touched} touched, {replied} replied ({rate:.0f}%), "
                        f"{meetings} meetings")

    with right:
        st.subheader("By Contact Type")
        type_data = []
        for ctype in sorted(set(ct.contact_type for ct in contacts)):
            ct_list = [ct for ct in contacts if ct.contact_type == ctype]
            touched = sum(1 for ct in ct_list if ct.touch_count > 0)
            replied = sum(1 for ct in ct_list if ct.dm_status == "replied")
            rate = (replied / touched * 100) if touched else 0
            type_data.append({"Type": ctype, "Count": len(ct_list), "Replied": replied, "Rate": f"{rate:.0f}%"})
        st.dataframe(type_data, use_container_width=True, hide_index=True)

    # Industry breakdown
    st.divider()
    st.subheader("By Industry")
    ind_data = []
    for ind in sorted(set(co.industry for co in companies)):
        ind_cos = [co for co in companies if co.industry == ind]
        avg_score = sum(co.lead_score for co in ind_cos) // len(ind_cos) if ind_cos else 0
        sap_count = sum(1 for co in ind_cos if co.sap_user == "Yes")
        ind_data.append({
            "Industry": ind,
            "Companies": len(ind_cos),
            "Avg Score": avg_score,
            "SAP Users": sap_count,
        })

    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.dataframe(ind_data, use_container_width=True, hide_index=True)
    with col_chart:
        fig = px.bar(ind_data, x="Industry", y="Companies", color="Avg Score",
                     color_continuous_scale="Viridis", title="Companies by Industry")
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Score distribution
    st.divider()
    st.subheader("Lead Score Distribution")
    col1, col2 = st.columns(2)
    with col1:
        co_scores = [co.lead_score for co in companies]
        fig = px.histogram(x=co_scores, nbins=10, title="Company Scores",
                           labels={"x": "Score", "y": "Count"})
        fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        ct_scores = [ct.lead_score for ct in contacts]
        fig = px.histogram(x=ct_scores, nbins=10, title="Contact Scores",
                           labels={"x": "Score", "y": "Count"})
        fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    # Pipeline value
    st.divider()
    try:
        from src.config import get_settings
        meeting_value = get_settings().meeting_pipeline_value
    except Exception:
        meeting_value = 50000
    total_value = stats["meetings"] * meeting_value
    st.subheader("Pipeline Value")
    st.metric(
        "Estimated Pipeline",
        f"AUD {total_value:,}",
        f"{stats['meetings']} meetings × AUD {meeting_value:,}",
    )

# ---------------------------------------------------------------------------
# Page: Settings
# ---------------------------------------------------------------------------

elif page == "⚙️ Settings":
    st.title("Settings & Connection Status")

    try:
        from src.config import get_settings, Settings
        settings = get_settings()
        status = settings.check_status()

        st.subheader("Environment Variables")
        table_data = []
        for field, is_set in status.items():
            table_data.append({
                "Variable": field.upper(),
                "Status": "✅ Configured" if is_set else "❌ Missing",
            })
        st.dataframe(table_data, use_container_width=True, hide_index=True)

        configured = sum(1 for v in status.values() if v)
        total = len(status)
        if configured == total:
            st.success(f"All {total} variables configured.")
        else:
            st.warning(f"{configured}/{total} configured. Fill in `.env` to enable all features.")

    except Exception as e:
        st.error(f"Cannot load settings: {e}")

    st.divider()
    st.subheader("Data Source")
    if data_source == "demo":
        st.info(
            "Currently using **demo data**. To connect to live Bitable data:\n\n"
            "1. Copy `.env.example` to `.env`\n"
            "2. Fill in Feishu credentials (`FEISHU_APP_ID`, `FEISHU_APP_SECRET`, etc.)\n"
            "3. Refresh this page\n\n"
            "The dashboard auto-detects and switches to live data when credentials are set."
        )
    else:
        st.success("Connected to **Feishu Bitable** (live data).")
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.subheader("CLI Commands")
    st.code("""
# Daily workflow
python -m src.cli pipeline check-acceptances
python -m src.cli pipeline daily
python -m src.cli pipeline generate-dms --touch day1
python -m src.cli pipeline warm --touch day1
python -m src.cli pipeline push-dms --touch day1 --confirm-all
python -m src.cli pipeline stats
    """, language="bash")
