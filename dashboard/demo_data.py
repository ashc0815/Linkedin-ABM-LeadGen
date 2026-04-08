"""Demo data generator for dashboard when Bitable is not connected."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from src.models import Company, Contact

_COMPANY_NAMES = [
    ("BHP Group", "Mining", 35000, "Melbourne", True, "Yes", "Unknown"),
    ("Rio Tinto", "Mining", 47000, "Melbourne", True, "Yes", "Yes"),
    ("Woolworths Group", "Other", 18000, "Sydney", True, "Unknown", "Unknown"),
    ("BlueScope Steel", "Manufacturing", 8500, "Wollongong", True, "Yes", "No"),
    ("Orica", "Manufacturing", 12000, "Melbourne", True, "Yes", "Unknown"),
    ("UNSW", "Education", 7000, "Sydney", False, "No", "No"),
    ("Deloitte Australia", "Professional Services", 10000, "Sydney", True, "Yes", "Yes"),
    ("Fortescue Metals", "Mining", 15000, "Perth", True, "Yes", "No"),
    ("Lendlease", "Other", 9000, "Sydney", True, "Unknown", "Unknown"),
    ("CSL Limited", "Manufacturing", 30000, "Melbourne", True, "Yes", "Unknown"),
    ("Qantas", "Other", 25000, "Sydney", True, "Yes", "Yes"),
    ("Macquarie Uni", "Education", 4000, "Sydney", False, "No", "No"),
    ("Aurecon", "Professional Services", 5500, "Melbourne", True, "Unknown", "Unknown"),
    ("Newcrest Mining", "Mining", 8000, "Melbourne", True, "Yes", "No"),
    ("Boral", "Manufacturing", 6500, "Sydney", True, "Yes", "Unknown"),
    ("Monash Uni", "Education", 8500, "Melbourne", False, "No", "No"),
    ("KPMG Australia", "Professional Services", 9000, "Sydney", True, "Yes", "Yes"),
    ("South32", "Mining", 6000, "Perth", True, "Yes", "No"),
    ("Incitec Pivot", "Manufacturing", 3500, "Melbourne", True, "Yes", "Unknown"),
    ("GrainCorp", "Manufacturing", 2800, "Sydney", False, "Unknown", "Unknown"),
]

_FIRST_NAMES = ["Sarah", "James", "Li", "Emma", "David", "Maria", "Wei", "John", "Anna", "Michael",
                "Rachel", "Tom", "Helen", "Mark", "Grace", "Peter", "Kate", "Andrew", "Fiona", "Chris"]
_LAST_NAMES = ["Chen", "Smith", "Wang", "Brown", "Lee", "Wilson", "Zhang", "Taylor", "Liu", "Martin",
               "Thompson", "White", "Yang", "Clark", "Xu", "Hall", "Wu", "Young", "Lin", "Moore"]

_CONTACT_TYPES = ["CFO", "Finance Director", "Financial Controller", "Finance Manager",
                   "Head of Finance", "Digital Transformation"]
_DM_STATUSES = ["not_started", "day1_queued", "day1_sent", "day7_queued", "day7_sent",
                "day14_queued", "day14_sent", "day21_sent", "replied", "meeting_booked", "rejected"]


def generate_demo_companies() -> list[Company]:
    companies = []
    for name, ind, emp, city, overseas, sap, concur in _COMPANY_NAMES:
        score = random.randint(20, 95)
        signals = {}
        if random.random() > 0.4:
            signals["recent_news"] = [{"title": f"{name} Q1 results strong"}]
        if random.random() > 0.5:
            signals["tech_stack_clues"] = ["sap"]
        statuses = ["未触达", "已触达", "已回复", "已约meeting"]
        companies.append(Company(
            company_name=name,
            linkedin_url=f"https://linkedin.com/company/{name.lower().replace(' ', '-')}/",
            website=f"https://{name.lower().replace(' ', '')}.com.au",
            industry=ind,
            employee_count=emp,
            hq_city=city,
            has_overseas_offices=overseas,
            sap_user=sap,
            uses_concur=concur,
            enrichment_signals=signals,
            outreach_status=random.choice(statuses),
            lead_score=score,
        ))
    return companies


def generate_demo_contacts(companies: list[Company]) -> list[Contact]:
    contacts = []
    now = datetime.now(timezone.utc)
    used_names = set()
    for co in companies:
        n_contacts = random.randint(1, 4)
        for _ in range(n_contacts):
            first = random.choice(_FIRST_NAMES)
            last = random.choice(_LAST_NAMES)
            name = f"{first} {last}"
            if name in used_names:
                continue
            used_names.add(name)
            ctype = random.choice(_CONTACT_TYPES)
            flow = random.choice(["re_activation", "cold_new"])
            status = random.choice(_DM_STATUSES)
            score = random.randint(15, 95)
            touch_count = random.randint(0, 4)
            last_touch = now - timedelta(days=random.randint(0, 30)) if touch_count > 0 else None
            next_touch = now + timedelta(days=random.randint(0, 14)) if status.endswith("_sent") else None

            drafts = []
            if touch_count > 0:
                drafts.append({
                    "touch": "day1",
                    "draft": f"Hi {first}, quick thought on T&E compliance for {co.industry.lower()} firms. Tony",
                    "generated_at": (now - timedelta(days=20)).isoformat(),
                    "sent_at": (now - timedelta(days=19)).isoformat() if touch_count >= 1 else None,
                })

            contacts.append(Contact(
                name=name,
                title=f"{ctype} at {co.company_name}",
                company_name=co.company_name,
                linkedin_url=f"https://linkedin.com/in/{first.lower()}{last.lower()}/",
                linkedin_provider_id=f"pid_{first.lower()}" if random.random() > 0.2 else None,
                profile_summary=f"{random.randint(10, 25)} years in {co.industry.lower()} finance" if random.random() > 0.3 else "",
                contact_type=ctype,
                flow_type=flow,
                dm_status=status,
                last_touch_date=last_touch,
                next_touch_date=next_touch,
                touch_count=touch_count,
                dm_drafts=drafts,
                is_existing_connection=flow == "re_activation",
                lead_score=score,
            ))
    return contacts
