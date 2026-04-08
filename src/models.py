"""Core Pydantic models for Company and Contact."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    company_name: str
    linkedin_url: str
    website: str | None = None
    industry: Literal[
        "Manufacturing",
        "Professional Services",
        "Education",
        "Mining",
        "Resources",
        "Other",
    ]
    employee_count: int = 0
    hq_city: str | None = None
    has_overseas_offices: bool = False
    sap_user: Literal["Yes", "No", "Unknown"] = "Unknown"
    uses_concur: Literal["Yes", "No", "Unknown"] = "Unknown"
    enrichment_signals: dict = Field(default_factory=dict)
    outreach_status: Literal["未触达", "已触达", "已回复", "已拒绝", "已约meeting"] = "未触达"
    source: Literal["Apify", "Manual", "Referral", "Existing_329"] = "Apify"
    lead_score: int = Field(default=0, ge=0, le=100)
    notes: str = ""


class Contact(BaseModel):
    name: str
    title: str
    company_name: str
    linkedin_url: str
    linkedin_provider_id: str | None = None
    profile_summary: str = ""
    contact_type: Literal[
        "CFO",
        "Finance Director",
        "Financial Controller",
        "Finance Manager",
        "Head of Finance",
        "Digital Transformation",
        "Other",
    ]
    flow_type: Literal["re_activation", "cold_new"]
    dm_status: Literal[
        "not_started",
        "draft_ready",
        "day1_queued",
        "day1_sent",
        "day7_queued",
        "day7_sent",
        "day14_queued",
        "day14_sent",
        "day21_queued",
        "day21_sent",
        "replied",
        "rejected",
        "meeting_booked",
    ] = "not_started"
    last_touch_date: datetime | None = None
    next_touch_date: datetime | None = None
    touch_count: int = 0
    reply_summary: str = ""
    dm_drafts: list[dict] = Field(default_factory=list)
    is_existing_connection: bool = False
    unipile_chat_id: str | None = None
    lead_score: int = Field(default=0, ge=0, le=100)
