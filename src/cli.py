"""Typer CLI entry point for linkedin-abm-agent."""

import csv
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.config import load_settings

console = Console()
app = typer.Typer(name="abm", help="LinkedIn ABM lead generation agent.")
pipeline_app = typer.Typer(help="Pipeline commands.")
app.add_typer(pipeline_app, name="pipeline")


FIELD_LABELS: dict[str, str] = {
    "apify_api_token": "Apify API",
    "feishu_app_id": "飞书 App ID",
    "feishu_app_secret": "飞书 App Secret",
    "feishu_bitable_app_token": "飞书 Bitable App Token",
    "feishu_companies_table_id": "飞书 Companies Table",
    "feishu_contacts_table_id": "飞书 Contacts Table",
    "anthropic_api_key": "Anthropic (Claude)",
    "unipile_api_key": "Unipile API Key",
    "unipile_dsn": "Unipile DSN",
    "unipile_account_id": "Unipile Account",
    "brave_search_api_key": "Brave Search API",
}


@pipeline_app.command("init")
def pipeline_init() -> None:
    """Check environment configuration and display connection status."""
    settings = load_settings()
    status = settings.check_status()

    # ---- Environment variables table ----
    table = Table(title="linkedin-abm-agent  Environment Status")
    table.add_column("Service", style="cyan", min_width=28)
    table.add_column("Variable", style="dim")
    table.add_column("Status", justify="center")

    configured = 0
    total = len(status)

    for field_name, is_set in status.items():
        label = FIELD_LABELS.get(field_name, field_name)
        badge = "[green]✔ Configured[/green]" if is_set else "[red]✘ Missing[/red]"
        table.add_row(label, field_name.upper(), badge)
        if is_set:
            configured += 1

    console.print()
    console.print(table)
    console.print()

    if configured == total:
        console.print(f"[bold green]All {total} variables configured. Ready to go![/bold green]")
    else:
        console.print(
            f"[bold yellow]{configured}/{total} configured. "
            f"Fill in missing variables in .env before running the pipeline.[/bold yellow]"
        )

    # ---- Bitable connection test ----
    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
    ])

    console.print()
    if not feishu_ready:
        console.print("[dim]Skipping Bitable connection test (Feishu credentials not set)[/dim]")
        return

    console.print("[bold]Testing Bitable connection...[/bold]")
    from src.bitable_client import BitableClient

    client = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    result = client.test_connection()

    bt_table = Table(title="Bitable Connection Test")
    bt_table.add_column("Check", style="cyan", min_width=20)
    bt_table.add_column("Result", justify="center")

    for key, label in [("auth", "认证"), ("companies", "Companies 表"), ("contacts", "Contacts 表")]:
        if result.get(key):
            bt_table.add_row(label, "[green]✔ OK[/green]")
        else:
            err = result.get(f"{key}_error", result.get("error", "failed"))
            bt_table.add_row(label, f"[red]✘ {err}[/red]")

    console.print(bt_table)
    console.print()


# ---------------------------------------------------------------------------
# pipeline scrape-companies
# ---------------------------------------------------------------------------

APIFY_DELAY_SECONDS = 3


@pipeline_app.command("scrape-companies")
def pipeline_scrape_companies(
    industry: str = typer.Option(
        "Manufacturing", help="Comma-separated industries to search"
    ),
    min_employees: int = typer.Option(200, help="Minimum employee count"),
    max_employees: int = typer.Option(5000, help="Maximum employee count"),
    location: str = typer.Option("Australia", help="Geographic location filter"),
    keyword: str = typer.Option("", help="Additional search keyword (e.g. 'SAP')"),
    max_results: int = typer.Option(100, help="Max results per industry keyword"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not write to Bitable"),
) -> None:
    """Scrape LinkedIn companies via Apify and write new ones to Bitable."""
    from src.apify_client import ApifyLinkedInClient, completeness_score
    from src.bitable_client import BitableClient
    from src.models import Company

    settings = load_settings()
    if not settings.apify_api_token:
        console.print("[red]APIFY_API_TOKEN not set. Aborting.[/red]")
        raise typer.Exit(1)

    apify = ApifyLinkedInClient(api_token=settings.apify_api_token)

    # Build Bitable client (may be unused in dry-run but we still need it for dedup)
    bitable: BitableClient | None = None
    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
    ])
    if feishu_ready and not dry_run:
        bitable = BitableClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            app_token=settings.feishu_bitable_app_token,
            companies_table_id=settings.feishu_companies_table_id,
            contacts_table_id=settings.feishu_contacts_table_id,
        )

    # ---- 1. Scrape per-industry keyword ----
    industries = [s.strip() for s in industry.split(",") if s.strip()]
    all_companies: list[Company] = []

    for idx, ind in enumerate(industries):
        search_kw = f"{ind} {keyword}".strip() if keyword else ind
        console.print(f"\n[bold cyan]Scraping:[/bold cyan] keyword={search_kw!r}  location={location}")

        try:
            batch = apify.scrape_companies(
                keyword=search_kw,
                location=location,
                min_employees=min_employees,
                max_employees=max_employees,
                max_results=max_results,
            )
        except Exception as exc:
            console.print(f"[red]Apify error for {search_kw!r}: {exc}[/red]")
            batch = []

        if not batch:
            console.print(
                f"[yellow]⚠ No results for {search_kw!r}. "
                f"Try broader keywords or a different location.[/yellow]"
            )

        all_companies.extend(batch)

        # Rate-limit between Apify calls
        if idx < len(industries) - 1:
            console.print(f"[dim]Waiting {APIFY_DELAY_SECONDS}s before next Apify call...[/dim]")
            time.sleep(APIFY_DELAY_SECONDS)

    # ---- 2. Deduplicate by linkedin_url ----
    seen_urls: dict[str, Company] = {}
    for c in all_companies:
        if c.linkedin_url not in seen_urls:
            seen_urls[c.linkedin_url] = c
    unique_companies = list(seen_urls.values())
    dup_count = len(all_companies) - len(unique_companies)

    console.print(
        f"\n[bold]Total scraped: {len(all_companies)} | "
        f"Unique: {len(unique_companies)} | Duplicates removed: {dup_count}[/bold]"
    )

    # ---- 3. Check existing in Bitable & categorise ----
    new_companies: list[Company] = []
    existing_count = 0
    incomplete_count = 0

    for company in unique_companies:
        # Check Bitable for existing (skip in dry-run if no bitable client)
        already_exists = False
        if bitable:
            try:
                found = bitable.find_company_by_linkedin_url(company.linkedin_url)
                if found:
                    already_exists = True
            except Exception:
                pass  # On error, treat as new

        if already_exists:
            existing_count += 1
        else:
            new_companies.append(company)

        pop, tot = completeness_score(company)
        if pop < tot:
            incomplete_count += 1

    # ---- 4. Results table ----
    result_table = Table(title="Scrape Results")
    result_table.add_column("公司名", style="cyan", max_width=30)
    result_table.add_column("行业")
    result_table.add_column("员工数", justify="right")
    result_table.add_column("城市")
    result_table.add_column("完整度", justify="center")
    result_table.add_column("状态", justify="center")

    for company in unique_companies:
        pop, tot = completeness_score(company)
        is_new = company in new_companies
        status_badge = "[green]新增[/green]" if is_new else "[dim]已存在[/dim]"
        completeness_badge = (
            f"[green]{pop}/{tot}[/green]" if pop == tot
            else f"[yellow]{pop}/{tot}[/yellow]"
        )
        result_table.add_row(
            company.company_name[:30],
            company.industry,
            str(company.employee_count),
            company.hq_city or "-",
            completeness_badge,
            status_badge,
        )

    console.print()
    console.print(result_table)

    # ---- 5. Write to Bitable (unless dry-run) ----
    if dry_run:
        console.print("\n[bold yellow]--dry-run: no records written to Bitable[/bold yellow]")
    elif bitable and new_companies:
        console.print(f"\n[bold]Writing {len(new_companies)} new companies to Bitable...[/bold]")
        fields_list = [bitable._company_to_fields(c) for c in new_companies]
        try:
            bitable.batch_create_records(bitable.companies_table_id, fields_list)
            console.print("[green]✔ Batch write complete[/green]")
        except Exception as exc:
            console.print(f"[red]✘ Batch write failed: {exc}[/red]")
    elif not bitable and not dry_run:
        console.print(
            "\n[yellow]Feishu credentials not fully configured — "
            "skipping Bitable write. Use --dry-run to preview.[/yellow]"
        )

    # ---- 6. Summary ----
    console.print(
        f"\n[bold]Summary: 新增 {len(new_companies)} 家, "
        f"跳过 {existing_count} 家重复, "
        f"{incomplete_count} 家数据不完整待补[/bold]"
    )


# ---------------------------------------------------------------------------
# pipeline import-existing
# ---------------------------------------------------------------------------

# Keywords (lowercased) used to filter finance-relevant LinkedIn connections
_FINANCE_KEYWORDS: list[str] = [
    "cfo",
    "chief financial officer",
    "finance director",
    "financial controller",
    "head of finance",
    "finance manager",
    "vp finance",
    "director of finance",
    "digital transformation",
    "chief digital officer",
    "head of digital",
]


def _is_finance_position(position: str) -> bool:
    lower = position.lower()
    return any(kw in lower for kw in _FINANCE_KEYWORDS)


@pipeline_app.command("import-existing")
def pipeline_import_existing(
    file: str = typer.Option(..., "--file", help="Path to LinkedIn Connections.csv export"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not write to Bitable"),
) -> None:
    """Import Tony's existing LinkedIn connections, filtering for finance contacts."""
    from src.apify_client import _infer_contact_type
    from src.bitable_client import COMPANY_FIELD_MAP, BitableClient
    from src.lead_scorer import score_contact as _score_contact
    from src.models import Company, Contact
    from src.unipile_client import UnipileClient

    # ---- Validate file ----
    csv_path = Path(file)
    if not csv_path.is_file():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    settings = load_settings()
    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )

    # Unipile (optional)
    unipile: UnipileClient | None = None
    unipile_ready = all([settings.unipile_api_key, settings.unipile_dsn, settings.unipile_account_id])
    if unipile_ready:
        unipile = UnipileClient(
            api_key=settings.unipile_api_key,
            dsn=settings.unipile_dsn,
            account_id=settings.unipile_account_id,
        )
    else:
        console.print("[yellow]Unipile credentials not set — skipping provider ID enrichment[/yellow]")

    # ---- 1. Read & filter CSV ----
    console.print(f"[bold]Reading {csv_path.name}...[/bold]")
    total_rows = 0
    finance_rows: list[dict] = []
    skipped_no_url = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            # Normalise header names (LinkedIn CSV has spaces: "First Name")
            first = (row.get("First Name") or row.get("first_name") or "").strip()
            last = (row.get("Last Name") or row.get("last_name") or "").strip()
            position = (row.get("Position") or row.get("position") or "").strip()
            company_name = (row.get("Company") or row.get("company") or "").strip()
            url = (row.get("URL") or row.get("url") or "").strip()

            if not _is_finance_position(position):
                continue

            if not url:
                skipped_no_url += 1
                continue

            finance_rows.append({
                "name": f"{first} {last}".strip(),
                "position": position,
                "company": company_name,
                "url": url,
            })

    console.print(
        f"  Total connections: {total_rows} | "
        f"Finance-relevant: {len(finance_rows)} | "
        f"Skipped (no URL): {skipped_no_url}"
    )

    if not finance_rows:
        console.print("[yellow]No finance-relevant contacts found in the CSV.[/yellow]")
        raise typer.Exit(0)

    # ---- 2. Build company lookup from Bitable ----
    console.print("[bold]Loading existing companies from Bitable...[/bold]")
    existing_company_records = bitable.list_records(bitable.companies_table_id)
    companies_by_name: dict[str, Company] = {}
    for rec in existing_company_records:
        try:
            c = bitable._fields_to_company(rec.get("fields", {}))
            companies_by_name[c.company_name] = c
        except Exception:
            pass
    console.print(f"  {len(companies_by_name)} companies loaded")

    # ---- 3. Process each finance contact ----
    type_counts: dict[str, int] = {}
    new_contacts: list[Contact] = []
    already_in_system = 0
    new_companies_added: set[str] = set()
    provider_resolved = 0

    for idx, row in enumerate(finance_rows, 1):
        name = row["name"]
        position = row["position"]
        company_name = row["company"]
        linkedin_url = row["url"]

        console.print(
            f"  [cyan][{idx}/{len(finance_rows)}][/cyan] {name} — {position} @ {company_name}",
            end="",
        )

        # 3a. Dedup against Bitable
        if not dry_run:
            existing = bitable.find_contact_by_linkedin_url(linkedin_url)
            if existing:
                already_in_system += 1
                console.print(" [dim](exists)[/dim]")
                continue

        # 3b. Infer contact_type
        contact_type = _infer_contact_type(position)
        type_counts[contact_type] = type_counts.get(contact_type, 0) + 1

        # 3c. Build Contact model
        ct = Contact(
            name=name,
            title=position,
            company_name=company_name,
            linkedin_url=linkedin_url,
            contact_type=contact_type,
            flow_type="re_activation",
            is_existing_connection=True,
        )

        # 3d. Unipile enrichment
        if unipile and not dry_run:
            try:
                pid = unipile.resolve_provider_id(linkedin_url)
                if pid:
                    ct.linkedin_provider_id = pid
                    provider_resolved += 1

                    # Confirm connection (should be True for existing connections)
                    unipile.check_is_connection(pid)

                    # Check for existing chat
                    chat_id = unipile.get_existing_chat(pid)
                    if chat_id:
                        ct.unipile_chat_id = chat_id

                    # Fetch profile summary
                    try:
                        profile = unipile.get_profile(pid)
                        ct.profile_summary = (
                            profile.get("about")
                            or profile.get("headline")
                            or ""
                        )[:500]
                    except Exception:
                        pass
            except Exception:
                pass

        # 3e. Match or create company
        if company_name and company_name not in companies_by_name and not dry_run:
            new_co = Company(
                company_name=company_name,
                linkedin_url="",
                industry="Other",
                source="Existing_329",
            )
            try:
                bitable.create_record(
                    bitable.companies_table_id,
                    bitable._company_to_fields(new_co),
                )
                companies_by_name[company_name] = new_co
                new_companies_added.add(company_name)
            except Exception:
                pass

        # 3f. Score the contact
        comp = companies_by_name.get(company_name, Company(
            company_name=company_name, linkedin_url="", industry="Other",
        ))
        ct.lead_score = min(_score_contact(ct, comp), 100)

        new_contacts.append(ct)
        console.print(" [green]✔[/green]")

    # ---- 4. Write contacts to Bitable ----
    if not dry_run and new_contacts:
        console.print(f"\n[bold]Writing {len(new_contacts)} contacts to Bitable...[/bold]")
        fields_list = [bitable._contact_to_fields(ct) for ct in new_contacts]
        try:
            bitable.batch_create_records(bitable.contacts_table_id, fields_list)
            console.print("[green]✔ Batch write complete[/green]")
        except Exception as exc:
            console.print(f"[red]✘ Batch write failed: {exc}[/red]")
    elif dry_run:
        console.print(f"\n[bold yellow]--dry-run: {len(new_contacts)} contacts would be written[/bold yellow]")

    # ---- 5. Results table ----
    result_table = Table(title="Import Results")
    result_table.add_column("姓名", style="cyan", max_width=20)
    result_table.add_column("职位", max_width=28)
    result_table.add_column("公司", max_width=22)
    result_table.add_column("类型", max_width=14)
    result_table.add_column("Score", justify="center", width=5)
    result_table.add_column("Provider ID", justify="center", width=11)

    for ct in new_contacts[:50]:  # Show first 50 to avoid huge output
        pid_badge = "[green]✔[/green]" if ct.linkedin_provider_id else "[dim]—[/dim]"
        sc = "green" if ct.lead_score >= 50 else "yellow" if ct.lead_score >= 25 else "dim"
        result_table.add_row(
            ct.name[:20],
            ct.title[:28],
            ct.company_name[:22],
            ct.contact_type[:14],
            f"[{sc}]{ct.lead_score}[/{sc}]",
            pid_badge,
        )

    if len(new_contacts) > 50:
        result_table.add_row("...", f"(+{len(new_contacts) - 50} more)", "", "", "", "")

    console.print()
    console.print(result_table)

    # ---- 6. Summary ----
    console.print(
        f"\n[bold]Imported: {len(new_contacts)} finance contacts "
        f"from {total_rows} total connections[/bold]"
    )

    # Type breakdown
    type_parts = []
    for ctype in ["CFO", "Finance Director", "Financial Controller", "Finance Manager",
                   "Head of Finance", "Digital Transformation", "Other"]:
        cnt = type_counts.get(ctype, 0)
        if cnt > 0:
            type_parts.append(f"{ctype}: {cnt}")
    if type_parts:
        console.print(f"  {', '.join(type_parts)}")

    console.print(f"  New companies added: {len(new_companies_added)}")
    console.print(f"  Already in system: {already_in_system}")
    if unipile:
        console.print(f"  Provider ID resolved: {provider_resolved}/{len(new_contacts)}")


# ---------------------------------------------------------------------------
# pipeline scrape-contacts
# ---------------------------------------------------------------------------


@pipeline_app.command("scrape-contacts")
def pipeline_scrape_contacts(
    status: str = typer.Option("未触达", help="Filter companies by outreach_status"),
    limit: int = typer.Option(20, help="Max companies to process"),
    company: Optional[str] = typer.Option(None, "--company", help="Process a single company by name"),
    min_score: int = typer.Option(0, "--min-score", help="Only process companies with lead_score >= this"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not write to Bitable"),
) -> None:
    """Scrape LinkedIn contacts for companies, enrich via Unipile, and write to Bitable."""
    from src.apify_client import ApifyLinkedInClient
    from src.bitable_client import COMPANY_FIELD_MAP, BitableClient
    from src.lead_scorer import score_contact as _score_contact
    from src.models import Company, Contact
    from src.unipile_client import UnipileClient

    settings = load_settings()
    if not settings.apify_api_token:
        console.print("[red]APIFY_API_TOKEN not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    apify = ApifyLinkedInClient(api_token=settings.apify_api_token)
    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )

    # Build Unipile client (optional — gracefully degrade if not configured)
    unipile: UnipileClient | None = None
    unipile_ready = all([settings.unipile_api_key, settings.unipile_dsn, settings.unipile_account_id])
    if unipile_ready:
        unipile = UnipileClient(
            api_key=settings.unipile_api_key,
            dsn=settings.unipile_dsn,
            account_id=settings.unipile_account_id,
        )
    else:
        console.print("[yellow]Unipile credentials not set — skipping provider ID / connection enrichment[/yellow]")

    # ---- 1. Fetch target companies from Bitable, sorted by lead_score desc ----
    if company:
        col = COMPANY_FIELD_MAP["company_name"]
        formula = f'CurrentValue.[{col}] = "{company}"'
    else:
        col = COMPANY_FIELD_MAP["outreach_status"]
        formula = f'CurrentValue.[{col}] = "{status}"'
    raw_records = bitable.list_records(bitable.companies_table_id, filter_formula=formula)

    companies_with_ids: list[tuple[str, Company]] = []
    for rec in raw_records:
        try:
            c = bitable._fields_to_company(rec.get("fields", {}))
            if c.lead_score >= min_score:
                companies_with_ids.append((rec["record_id"], c))
        except Exception:
            pass

    # Sort by lead_score descending, apply limit
    companies_with_ids.sort(key=lambda x: x[1].lead_score, reverse=True)
    companies_with_ids = companies_with_ids[:limit]

    if not companies_with_ids:
        console.print("[yellow]No companies found matching the filter.[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"\n[bold]Processing {len(companies_with_ids)} companies "
        f"(sorted by lead_score, min_score={min_score})...[/bold]\n"
    )

    # ---- 2. Per-company loop ----
    total_new = 0
    total_existing = 0
    total_reactivation = 0
    total_cold = 0

    result_table = Table(title="Contact Scrape Results")
    result_table.add_column("公司", style="cyan", max_width=24)
    result_table.add_column("Score", justify="center", width=5)
    result_table.add_column("Found", justify="center", width=5)
    result_table.add_column("New", justify="center", width=5)
    result_table.add_column("Existing", justify="center", width=8)
    result_table.add_column("Re-act", justify="center", width=6)
    result_table.add_column("Cold", justify="center", width=5)

    for co_idx, (_co_rec_id, comp) in enumerate(companies_with_ids, 1):
        console.print(
            f"[cyan][{co_idx}/{len(companies_with_ids)}][/cyan] "
            f"Processing {comp.company_name} (score={comp.lead_score})...",
            end=" ",
        )

        # 2a. Scrape contacts via Apify
        try:
            scraped = apify.scrape_contacts_for_company(comp.company_name, comp.linkedin_url)
        except Exception as exc:
            console.print(f"[red]Apify error: {exc}[/red]")
            scraped = []

        # 2b. Deduplicate against Bitable
        new_contacts: list[Contact] = []
        existing_in_bitable = 0
        for ct in scraped:
            found = bitable.find_contact_by_linkedin_url(ct.linkedin_url) if not dry_run else None
            if found:
                existing_in_bitable += 1
            else:
                new_contacts.append(ct)

        # 2c. Enrich each new contact via Unipile
        co_reactivation = 0
        co_cold = 0
        for ct in new_contacts:
            if unipile and not dry_run:
                try:
                    pid = unipile.resolve_provider_id(ct.linkedin_url)
                    if pid:
                        ct.linkedin_provider_id = pid

                        # Check connection status
                        if unipile.check_is_connection(pid):
                            ct.is_existing_connection = True
                            ct.flow_type = "re_activation"

                        # Supplement profile_summary if empty
                        if not ct.profile_summary:
                            try:
                                profile = unipile.get_profile(pid)
                                ct.profile_summary = (
                                    profile.get("about")
                                    or profile.get("headline")
                                    or ""
                                )[:500]
                            except Exception:
                                pass

                        # Check for existing chat
                        chat_id = unipile.get_existing_chat(pid)
                        if chat_id:
                            ct.unipile_chat_id = chat_id
                except Exception as exc:
                    logger_msg = f"Unipile enrichment failed for {ct.name}: {exc}"
                    logger_msg  # logged below if needed
                    pass  # Graceful degradation

            # Score the contact
            ct.lead_score = min(_score_contact(ct, comp), 100)

            if ct.flow_type == "re_activation":
                co_reactivation += 1
            else:
                co_cold += 1

        console.print(
            f"found {len(scraped)} contacts "
            f"({len(new_contacts)} new, {existing_in_bitable} existing)"
        )

        # 2d. Write to Bitable
        if not dry_run and new_contacts:
            fields_list = [bitable._contact_to_fields(ct) for ct in new_contacts]
            try:
                bitable.batch_create_records(bitable.contacts_table_id, fields_list)
            except Exception as exc:
                console.print(f"  [red]✘ Batch write failed: {exc}[/red]")

        # Track totals
        total_new += len(new_contacts)
        total_existing += existing_in_bitable
        total_reactivation += co_reactivation
        total_cold += co_cold

        result_table.add_row(
            comp.company_name[:24],
            str(comp.lead_score),
            str(len(scraped)),
            str(len(new_contacts)),
            str(existing_in_bitable),
            str(co_reactivation) if co_reactivation else "-",
            str(co_cold) if co_cold else "-",
        )

    # ---- 3. Summary ----
    console.print()
    console.print(result_table)

    if dry_run:
        console.print("\n[bold yellow]--dry-run: no records written to Bitable[/bold yellow]")

    console.print(
        f"\n[bold]Total: {total_new} new contacts "
        f"({total_reactivation} re_activation, {total_cold} cold_new), "
        f"{total_existing} skipped duplicates[/bold]"
    )


# ---------------------------------------------------------------------------
# pipeline enrich
# ---------------------------------------------------------------------------


@pipeline_app.command("enrich")
def pipeline_enrich(
    status: str = typer.Option("未触达", help="Filter companies by outreach_status"),
    limit: int = typer.Option(50, help="Max companies to enrich"),
    company: Optional[str] = typer.Option(None, "--company", help="Enrich a single company by name"),
    force: bool = typer.Option(False, "--force", help="Re-enrich companies that already have signals"),
) -> None:
    """Enrich company data via Brave Search and website analysis."""
    from src.bitable_client import COMPANY_FIELD_MAP, BitableClient
    from src.enrichment import EnrichmentService, enrichment_summary
    from src.models import Company

    settings = load_settings()
    if not settings.brave_search_api_key:
        console.print("[red]BRAVE_SEARCH_API_KEY not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    enricher = EnrichmentService(brave_api_key=settings.brave_search_api_key)

    # ---- 1. Fetch companies from Bitable ----
    if company:
        col = COMPANY_FIELD_MAP["company_name"]
        formula = f'CurrentValue.[{col}] = "{company}"'
        raw_records = bitable.list_records(bitable.companies_table_id, filter_formula=formula)
    else:
        col = COMPANY_FIELD_MAP["outreach_status"]
        formula = f'CurrentValue.[{col}] = "{status}"'
        raw_records = bitable.list_records(bitable.companies_table_id, filter_formula=formula)

    if not raw_records:
        console.print("[yellow]No companies found matching the filter.[/yellow]")
        raise typer.Exit(0)

    # Parse into (record_id, Company) pairs
    candidates: list[tuple[str, Company]] = []
    for rec in raw_records:
        try:
            c = bitable._fields_to_company(rec.get("fields", {}))
            candidates.append((rec["record_id"], c))
        except Exception as exc:
            logger_name = rec.get("fields", {}).get(COMPANY_FIELD_MAP["company_name"], "?")
            console.print(f"[dim]Skipping unparseable record {logger_name}: {exc}[/dim]")

    # ---- 2. Filter already-enriched (unless --force) ----
    if not force:
        candidates = [
            (rid, c) for rid, c in candidates if not c.enrichment_signals
        ]

    # Apply limit
    candidates = candidates[:limit]

    if not candidates:
        console.print("[yellow]No companies need enrichment (use --force to re-enrich).[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"\n[bold]Enriching {len(candidates)} companies "
        f"(~{len(candidates) * 5} Brave API calls, ~{len(candidates) * 5}s)...[/bold]\n"
    )

    # ---- 3. Enrich + score each company ----
    from src.lead_scorer import score_company as _score_company

    enriched: list[tuple[str, Company]] = []
    sap_count = 0
    concur_count = 0

    for idx, (record_id, comp) in enumerate(candidates, 1):
        console.print(
            f"[cyan][{idx}/{len(candidates)}][/cyan] Enriching {comp.company_name}...",
            end=" ",
        )
        comp = enricher.enrich_company(comp)
        comp.lead_score = _score_company(comp)
        enriched.append((record_id, comp))

        summary = enrichment_summary(comp)
        console.print(f"[green]✅[/green] {summary}  [dim](score={comp.lead_score})[/dim]")

        if comp.sap_user == "Yes":
            sap_count += 1
        if comp.uses_concur == "Yes":
            concur_count += 1

    # ---- 4. Update Bitable ----
    console.print(f"\n[bold]Updating {len(enriched)} records in Bitable...[/bold]")
    update_ok = 0
    for record_id, comp in enriched:
        try:
            fields = bitable._company_to_fields(comp)
            bitable.update_record(bitable.companies_table_id, record_id, fields)
            update_ok += 1
        except Exception as exc:
            console.print(f"[red]✘ Failed to update {comp.company_name}: {exc}[/red]")

    # ---- 5. Results table ----
    result_table = Table(title="Enrichment Results")
    result_table.add_column("公司名", style="cyan", max_width=30)
    result_table.add_column("SAP?", justify="center")
    result_table.add_column("Concur?", justify="center")
    result_table.add_column("新闻数", justify="center")
    result_table.add_column("海外办公室?", justify="center")
    result_table.add_column("Lead Score", justify="center")

    for _, comp in enriched:
        news_count = len(comp.enrichment_signals.get("recent_news", []))

        sap_badge = "[green]Yes[/green]" if comp.sap_user == "Yes" else "[dim]No[/dim]"
        concur_badge = "[green]Yes[/green]" if comp.uses_concur == "Yes" else "[dim]No[/dim]"
        overseas_badge = "[green]Yes[/green]" if comp.has_overseas_offices else "[dim]No[/dim]"
        score_color = "green" if comp.lead_score >= 50 else "yellow" if comp.lead_score >= 25 else "dim"

        result_table.add_row(
            comp.company_name[:30],
            sap_badge,
            concur_badge,
            str(news_count) if news_count else "-",
            overseas_badge,
            f"[{score_color}]{comp.lead_score}[/{score_color}]",
        )

    console.print()
    console.print(result_table)

    # ---- 6. Summary ----
    console.print(
        f"\n[bold]Enriched {update_ok}/{len(enriched)} companies, "
        f"{sap_count} confirmed SAP users, "
        f"{concur_count} confirmed Concur users[/bold]"
    )


# ---------------------------------------------------------------------------
# pipeline generate-dms
# ---------------------------------------------------------------------------

# Maps touch → the dm_status contacts must be in to receive that touch
_TOUCH_STATUS_MAP: dict[str, str] = {
    "day1": "not_started",
    "day7": "day1_sent",
    "day14": "day7_sent",
    "day21": "day14_sent",
}


@pipeline_app.command("generate-dms")
def pipeline_generate_dms(
    touch: str = typer.Option(..., help="Touch point: day1, day7, day14, day21"),
    batch_size: int = typer.Option(10, help="Max contacts to process"),
    flow_type: Optional[str] = typer.Option(None, "--flow-type", help="Filter by flow_type (re_activation or cold_new)"),
    contact_name: Optional[str] = typer.Option(None, "--contact", help="Generate for a single contact by name"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Overwrite existing drafts for this touch"),
    min_score: int = typer.Option(0, "--min-score", help="Only contacts with lead_score >= this"),
) -> None:
    """Generate personalised LinkedIn DMs using Claude."""
    from datetime import date

    from src.bitable_client import CONTACT_FIELD_MAP, COMPANY_FIELD_MAP, BitableClient
    from src.dm_generator import DMGenerator, make_draft_entry
    from src.models import Company, Contact

    if touch not in _TOUCH_STATUS_MAP:
        console.print(f"[red]Invalid touch: {touch!r}. Must be day1/day7/day14/day21.[/red]")
        raise typer.Exit(1)

    settings = load_settings()
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    generator = DMGenerator(anthropic_api_key=settings.anthropic_api_key)

    # ---- 1. Build company lookup ----
    console.print("[bold]Loading companies...[/bold]")
    raw_co = bitable.list_records(bitable.companies_table_id)
    companies_map: dict[str, Company] = {}
    for rec in raw_co:
        try:
            c = bitable._fields_to_company(rec.get("fields", {}))
            companies_map[c.company_name] = c
        except Exception:
            pass

    # ---- 2. Query contacts ----
    required_status = _TOUCH_STATUS_MAP[touch]

    if contact_name:
        col = CONTACT_FIELD_MAP["name"]
        formula = f'CurrentValue.[{col}] = "{contact_name}"'
    else:
        col = CONTACT_FIELD_MAP["dm_status"]
        formula = f'CurrentValue.[{col}] = "{required_status}"'

        # For day7+ add next_touch_date filter
        if touch != "day1":
            today_str = date.today().isoformat()
            date_col = CONTACT_FIELD_MAP["next_touch_date"]
            formula = f'AND({formula}, CurrentValue.[{date_col}] <= "{today_str}")'

        # Add flow_type filter if specified
        if flow_type:
            flow_col = CONTACT_FIELD_MAP["flow_type"]
            formula = f'AND({formula}, CurrentValue.[{flow_col}] = "{flow_type}")'

    raw_contacts = bitable.list_records(bitable.contacts_table_id, filter_formula=formula)

    # Parse into (record_id, Contact)
    candidates: list[tuple[str, Contact]] = []
    for rec in raw_contacts:
        try:
            ct = bitable._fields_to_contact(rec.get("fields", {}))
            if ct.lead_score >= min_score:
                candidates.append((rec["record_id"], ct))
        except Exception:
            pass

    # Sort by lead_score descending, apply batch_size
    candidates.sort(key=lambda x: x[1].lead_score, reverse=True)
    candidates = candidates[:batch_size]

    if not candidates:
        console.print(f"[yellow]No contacts found for {touch} (status={required_status}).[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"\n[bold]Generating {touch} DMs for {len(candidates)} contacts...[/bold]\n"
    )

    # ---- 3. Generate DMs ----
    generated = 0
    reactivation_count = 0
    cold_count = 0

    result_table = Table(title=f"Generated DMs — {touch}")
    result_table.add_column("Contact", style="cyan", max_width=20)
    result_table.add_column("Company", max_width=18)
    result_table.add_column("Score", justify="center", width=5)
    result_table.add_column("Flow", width=10)
    result_table.add_column("Draft", max_width=60)

    for idx, (record_id, ct) in enumerate(candidates, 1):
        # Skip if draft already exists for this touch (unless --regenerate)
        if not regenerate:
            existing_drafts = [d for d in ct.dm_drafts if d.get("touch") == touch]
            if existing_drafts:
                console.print(
                    f"  [dim][{idx}/{len(candidates)}] {ct.name} — "
                    f"draft for {touch} already exists (use --regenerate)[/dim]"
                )
                continue

        comp = companies_map.get(ct.company_name, Company(
            company_name=ct.company_name, linkedin_url="", industry="Other",
        ))

        console.print(
            f"  [cyan][{idx}/{len(candidates)}][/cyan] {ct.name} "
            f"({ct.contact_type}, {ct.company_name}) — "
            f"Score: {ct.lead_score} — {ct.flow_type} — {touch}...",
            end=" ",
        )

        try:
            draft = generator.generate_dm(ct, comp, touch)
        except Exception as exc:
            console.print(f"[red]error: {exc}[/red]")
            continue

        console.print(f"[green]OK[/green] ({len(draft)} chars)")

        # Update dm_drafts and dm_status
        if regenerate:
            ct.dm_drafts = [d for d in ct.dm_drafts if d.get("touch") != touch]
        ct.dm_drafts.append(make_draft_entry(touch, draft))
        ct.dm_status = f"{touch}_queued"

        # Write to Bitable
        try:
            fields = bitable._contact_to_fields(ct)
            bitable.update_record(bitable.contacts_table_id, record_id, fields)
        except Exception as exc:
            console.print(f"    [red]Bitable update failed: {exc}[/red]")

        generated += 1
        if ct.flow_type == "re_activation":
            reactivation_count += 1
        else:
            cold_count += 1

        # Truncate draft for table display
        display_draft = draft[:57] + "..." if len(draft) > 60 else draft
        flow_badge = "[green]re-act[/green]" if ct.flow_type == "re_activation" else "[dim]cold[/dim]"
        result_table.add_row(
            ct.name[:20],
            ct.company_name[:18],
            str(ct.lead_score),
            flow_badge,
            display_draft,
        )

    # ---- 4. Summary ----
    console.print()
    console.print(result_table)
    console.print(
        f"\n[bold]Generated {generated} drafts "
        f"({reactivation_count} re_activation, {cold_count} cold_new)[/bold]"
    )


# ---------------------------------------------------------------------------
# pipeline warm
# ---------------------------------------------------------------------------


@pipeline_app.command("warm")
def pipeline_warm(
    touch: str = typer.Option(..., help="Touch point whose queue to warm (day1, day7, day14, day21)"),
    limit: int = typer.Option(10, help="Max contacts to warm"),
) -> None:
    """Warm engagement before sending DMs — view profiles and like posts."""
    import random

    from src.bitable_client import CONTACT_FIELD_MAP, BitableClient
    from src.models import Contact
    from src.unipile_client import UnipileClient

    if touch not in _TOUCH_STATUS_MAP:
        console.print(f"[red]Invalid touch: {touch!r}[/red]")
        raise typer.Exit(1)

    settings = load_settings()
    unipile_ready = all([settings.unipile_api_key, settings.unipile_dsn, settings.unipile_account_id])
    if not unipile_ready:
        console.print("[red]Unipile credentials not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    unipile = UnipileClient(
        api_key=settings.unipile_api_key,
        dsn=settings.unipile_dsn,
        account_id=settings.unipile_account_id,
    )

    # Fetch contacts in queued state
    queued_status = f"{touch}_queued"
    col = CONTACT_FIELD_MAP["dm_status"]
    formula = f'CurrentValue.[{col}] = "{queued_status}"'
    raw = bitable.list_records(bitable.contacts_table_id, filter_formula=formula)

    candidates: list[tuple[str, Contact]] = []
    for rec in raw:
        try:
            ct = bitable._fields_to_contact(rec.get("fields", {}))
            if ct.linkedin_provider_id:
                candidates.append((rec["record_id"], ct))
        except Exception:
            pass

    candidates = candidates[:limit]

    if not candidates:
        console.print(f"[yellow]No contacts with provider_id in status {queued_status}.[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"\n[bold]Warming {len(candidates)} contacts before {touch} DMs...[/bold]\n"
    )

    viewed = 0
    liked = 0

    for idx, (_rid, ct) in enumerate(candidates, 1):
        pid = ct.linkedin_provider_id
        console.print(
            f"  [cyan][{idx}/{len(candidates)}][/cyan] {ct.name} ({ct.company_name})",
            end="",
        )

        # Step 1: View profile
        try:
            if unipile.view_profile(pid):
                viewed += 1
                console.print(" — viewed", end="")
        except Exception:
            pass

        # Random delay 5-10s
        delay_view = random.uniform(5, 10)
        time.sleep(delay_view)

        # Step 2: Like most recent post
        try:
            posts = unipile.get_recent_posts(pid, limit=1)
            if posts:
                post_id = posts[0].get("id") or posts[0].get("post_id")
                if post_id and unipile.react_to_post(post_id):
                    liked += 1
                    console.print(" — liked post", end="")
        except Exception:
            pass

        console.print(" [green]✔[/green]")

        # Random delay 3-8s between contacts
        if idx < len(candidates):
            time.sleep(random.uniform(3, 8))

    console.print(
        f"\n[bold]Warming complete: {viewed} profiles viewed, "
        f"{liked} posts liked[/bold]"
    )
    console.print(
        "\n[bold yellow]Tip: Wait 2-4 hours before sending DMs "
        "to let contacts see Tony's profile visit notification.[/bold yellow]"
    )


# ---------------------------------------------------------------------------
# pipeline push-dms
# ---------------------------------------------------------------------------

DAILY_SEND_LIMIT = 80
SEND_DELAY_MIN = 20  # seconds between sends
SEND_DELAY_MAX = 40


@pipeline_app.command("push-dms")
def pipeline_push_dms(
    touch: str = typer.Option(..., help="Touch point: day1, day7, day14, day21"),
    flow_type: Optional[str] = typer.Option(None, "--flow-type", help="Filter by flow_type"),
    contact_name: Optional[str] = typer.Option(None, "--contact", help="Send to a single contact"),
    confirm_all: bool = typer.Option(False, "--confirm-all", help="Skip per-message confirmation"),
    max_sends: int = typer.Option(25, "--max-sends", help="Max messages this session"),
) -> None:
    """Send queued DM drafts via Unipile."""
    import random
    from datetime import date, datetime, timedelta, timezone

    from rich.panel import Panel

    from src.bitable_client import CONTACT_FIELD_MAP, BitableClient
    from src.models import Company, Contact
    from src.unipile_client import UnipileClient, UnipileRateLimitError

    if touch not in _TOUCH_STATUS_MAP:
        console.print(f"[red]Invalid touch: {touch!r}[/red]")
        raise typer.Exit(1)

    settings = load_settings()
    unipile_ready = all([settings.unipile_api_key, settings.unipile_dsn, settings.unipile_account_id])
    if not unipile_ready:
        console.print("[red]Unipile credentials not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    unipile = UnipileClient(
        api_key=settings.unipile_api_key,
        dsn=settings.unipile_dsn,
        account_id=settings.unipile_account_id,
    )

    # ---- 1. Determine daily limit (warmup vs normal) ----
    daily_limit = DAILY_SEND_LIMIT
    if settings.warmup_mode:
        daily_limit = settings.warmup_daily_limit
        console.print(
            f"[yellow]WARMUP MODE active — daily limit: "
            f"{daily_limit} (set WARMUP_MODE=false after 2 weeks)[/yellow]"
        )

    # ---- 2. Check daily send count ----
    today_str = date.today().isoformat()
    date_col = CONTACT_FIELD_MAP["last_touch_date"]
    today_formula = f'CurrentValue.[{date_col}] = "{today_str}"'
    today_records = bitable.list_records(bitable.contacts_table_id, filter_formula=today_formula)
    sent_today = len(today_records)

    if sent_today >= daily_limit:
        console.print(
            f"[bold red]Daily limit reached ({sent_today}/{daily_limit} sent today). "
            f"Stop to protect the LinkedIn account.[/bold red]"
        )
        raise typer.Exit(1)

    remaining = daily_limit - sent_today
    effective_max = min(max_sends, remaining)
    if effective_max < max_sends:
        console.print(
            f"[yellow]Daily quota: {sent_today}/{daily_limit} used. "
            f"Capping this session to {effective_max} sends.[/yellow]"
        )

    # ---- 3. Fetch queued contacts ----
    queued_status = f"{touch}_queued"
    if contact_name:
        name_col = CONTACT_FIELD_MAP["name"]
        formula = f'CurrentValue.[{name_col}] = "{contact_name}"'
    else:
        status_col = CONTACT_FIELD_MAP["dm_status"]
        formula = f'CurrentValue.[{status_col}] = "{queued_status}"'
        if flow_type:
            flow_col = CONTACT_FIELD_MAP["flow_type"]
            formula = f'AND({formula}, CurrentValue.[{flow_col}] = "{flow_type}")'

    raw_contacts = bitable.list_records(bitable.contacts_table_id, filter_formula=formula)

    contacts_with_ids: list[tuple[str, Contact]] = []
    for rec in raw_contacts:
        try:
            ct = bitable._fields_to_contact(rec.get("fields", {}))
            contacts_with_ids.append((rec["record_id"], ct))
        except Exception:
            pass

    contacts_with_ids.sort(key=lambda x: x[1].lead_score, reverse=True)
    contacts_with_ids = contacts_with_ids[:effective_max]

    if not contacts_with_ids:
        console.print(f"[yellow]No contacts in {queued_status} status.[/yellow]")
        raise typer.Exit(0)

    # Build company lookup
    raw_co = bitable.list_records(bitable.companies_table_id)
    companies_map: dict[str, Company] = {}
    for rec in raw_co:
        try:
            c = bitable._fields_to_company(rec.get("fields", {}))
            companies_map[c.company_name] = c
        except Exception:
            pass

    console.print(
        f"\n[bold]Sending {touch} DMs for {len(contacts_with_ids)} contacts "
        f"(daily: {sent_today}/{daily_limit}"
        f"{' [warmup]' if settings.warmup_mode else ''})...[/bold]\n"
    )

    # ---- 4. Send loop ----
    sent_count = 0
    failed_count = 0
    skipped_count = 0
    rate_limited = False

    _NEXT_TOUCH_DAYS = {"day1": 7, "day7": 7, "day14": 7, "day21": 0}
    _SENT_STATUS = {"day1": "day1_sent", "day7": "day7_sent", "day14": "day14_sent", "day21": "day21_sent"}

    for idx, (record_id, ct) in enumerate(contacts_with_ids, 1):
        # Find the draft for this touch
        draft_entry = None
        for d in ct.dm_drafts:
            if d.get("touch") == touch:
                draft_entry = d
                break
        if not draft_entry:
            console.print(f"  [dim][{idx}] {ct.name} — no {touch} draft, skipping[/dim]")
            skipped_count += 1
            continue

        draft_text = draft_entry["draft"]

        # ---- Review panel ----
        comp = companies_map.get(ct.company_name)
        comp_info = f"{comp.industry}, {comp.employee_count} emp" if comp else "—"
        panel_text = (
            f"[bold]{ct.name}[/bold] — {ct.title}\n"
            f"{ct.company_name} ({comp_info})\n"
            f"Score: {ct.lead_score} | Flow: {ct.flow_type} | Touch: {touch}\n"
            f"Provider ID: {ct.linkedin_provider_id or '—'}\n\n"
            f"[italic]{draft_text}[/italic]"
        )
        console.print(Panel(panel_text, title=f"[{idx}/{len(contacts_with_ids)}] Review DM", border_style="cyan"))

        # ---- Confirm ----
        if not confirm_all:
            action = typer.prompt("Send? [y]es / [s]kip / [e]dit / [q]uit", default="y")
            action = action.strip().lower()
            if action == "q":
                console.print("[yellow]Quitting send session.[/yellow]")
                break
            if action == "s":
                skipped_count += 1
                continue
            if action == "e":
                draft_text = typer.prompt("Enter edited DM text")

        # ---- Send via Unipile ----
        if not ct.linkedin_provider_id:
            console.print(f"  [red]No provider_id — cannot send[/red]")
            failed_count += 1
            continue

        send_ok = False
        try:
            if ct.flow_type == "cold_new" and touch == "day1":
                # Cold new Day 1 → connection request with note
                result = unipile.send_connection_request(ct.linkedin_provider_id, note=draft_text)
                chat_id = result.get("id") or result.get("chat_id")
                if chat_id:
                    ct.unipile_chat_id = chat_id
                send_ok = True
            else:
                # All other cases → send_message into existing chat
                # (cold_new day7/14/21 means connection was accepted)
                # (re_activation any touch → already connected)
                if not ct.unipile_chat_id:
                    ct.unipile_chat_id = unipile.get_existing_chat(ct.linkedin_provider_id)
                if not ct.unipile_chat_id:
                    console.print(f"  [red]No chat found for {ct.name} — cannot send[/red]")
                    failed_count += 1
                    continue
                unipile.send_message(ct.unipile_chat_id, draft_text)
                send_ok = True

        except UnipileRateLimitError:
            rate_limited = True
            remaining_queue = len(contacts_with_ids) - idx
            console.print(
                f"\n[bold red]429 Rate limit hit! "
                f"Stopping session immediately. "
                f"{remaining_queue} contacts still in queue.[/bold red]"
            )
            break

        except Exception as exc:
            console.print(f"  [red]Send failed: {exc}[/red]")
            failed_count += 1
            continue

        if send_ok:
            sent_count += 1
            now = datetime.now(timezone.utc)

            # Update draft sent_at
            for d in ct.dm_drafts:
                if d.get("touch") == touch:
                    d["sent_at"] = now.isoformat()
                    break

            ct.dm_status = _SENT_STATUS[touch]
            ct.last_touch_date = now
            ct.touch_count += 1

            # Schedule next touch
            next_days = _NEXT_TOUCH_DAYS.get(touch, 0)
            if next_days > 0:
                ct.next_touch_date = now + timedelta(days=next_days)
            else:
                ct.next_touch_date = None

            # Write back to Bitable
            try:
                fields = bitable._contact_to_fields(ct)
                bitable.update_record(bitable.contacts_table_id, record_id, fields)
            except Exception as exc:
                console.print(f"  [red]Bitable update failed: {exc}[/red]")

            console.print(f"  [green]✔ Sent to {ct.name}[/green]")

            # Human-like delay between sends (skip after last one)
            if idx < len(contacts_with_ids):
                delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
                console.print(f"  [dim]Waiting {delay:.0f}s...[/dim]")
                time.sleep(delay)

    # ---- 5. Summary ----
    total_today = sent_today + sent_count
    console.print(
        f"\n[bold]Push complete: {sent_count} sent, "
        f"{skipped_count} skipped, {failed_count} failed "
        f"(daily total: {total_today}/{daily_limit})[/bold]"
    )
    if rate_limited:
        console.print(
            "[bold red]Session stopped due to 429 rate limit. "
            "Wait at least 15 minutes before retrying.[/bold red]"
        )


# ---------------------------------------------------------------------------
# pipeline check-acceptances
# ---------------------------------------------------------------------------


@pipeline_app.command("check-acceptances")
def pipeline_check_acceptances(
    withdraw_after_days: int = typer.Option(14, help="Withdraw connection request after N days"),
) -> None:
    """Check pending cold_new connection requests for acceptance or timeout."""
    from datetime import date, datetime, timedelta, timezone

    from src.bitable_client import CONTACT_FIELD_MAP, BitableClient
    from src.models import Contact
    from src.unipile_client import UnipileClient

    settings = load_settings()
    unipile_ready = all([settings.unipile_api_key, settings.unipile_dsn, settings.unipile_account_id])
    if not unipile_ready:
        console.print("[red]Unipile credentials not set. Aborting.[/red]")
        raise typer.Exit(1)

    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_contacts_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )
    unipile = UnipileClient(
        api_key=settings.unipile_api_key,
        dsn=settings.unipile_dsn,
        account_id=settings.unipile_account_id,
    )

    # ---- 1. Fetch cold_new contacts with day1_sent ----
    status_col = CONTACT_FIELD_MAP["dm_status"]
    flow_col = CONTACT_FIELD_MAP["flow_type"]
    formula = (
        f'AND(CurrentValue.[{status_col}] = "day1_sent", '
        f'CurrentValue.[{flow_col}] = "cold_new")'
    )
    raw = bitable.list_records(bitable.contacts_table_id, filter_formula=formula)

    contacts: list[tuple[str, Contact]] = []
    for rec in raw:
        try:
            ct = bitable._fields_to_contact(rec.get("fields", {}))
            contacts.append((rec["record_id"], ct))
        except Exception:
            pass

    if not contacts:
        console.print("[yellow]No pending cold_new connection requests found.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Checking {len(contacts)} pending connection requests...[/bold]\n")

    # ---- 2. Check each contact ----
    accepted_count = 0
    waiting_count = 0
    withdrawn_count = 0
    replied_count = 0
    waiting_days: list[int] = []
    today = date.today()
    now = datetime.now(timezone.utc)

    result_table = Table(title="Connection Request Status")
    result_table.add_column("Contact", style="cyan", max_width=20)
    result_table.add_column("Company", max_width=18)
    result_table.add_column("Sent", width=10)
    result_table.add_column("Days", justify="center", width=5)
    result_table.add_column("Status", justify="center", max_width=20)

    for idx, (record_id, ct) in enumerate(contacts, 1):
        pid = ct.linkedin_provider_id
        if not pid:
            console.print(f"  [dim][{idx}] {ct.name} — no provider_id, skipping[/dim]")
            continue

        # Calculate days since sent
        if ct.last_touch_date:
            days_waiting = (today - ct.last_touch_date.date()).days
        else:
            days_waiting = 0

        sent_date_str = ct.last_touch_date.strftime("%Y-%m-%d") if ct.last_touch_date else "—"

        # Check if connection was accepted
        is_connected = unipile.check_is_connection(pid)

        if is_connected:
            # ---- ACCEPTED ----
            ct.is_existing_connection = True

            # Check if they replied (sent a message back)
            has_reply = False
            if ct.unipile_chat_id:
                try:
                    messages = unipile.get_messages(ct.unipile_chat_id)
                    # Look for messages not sent by Tony (i.e. inbound)
                    for msg in messages:
                        sender = msg.get("sender_id") or msg.get("from_attendee", {}).get("provider_id", "")
                        if sender and sender != "self" and sender == pid:
                            has_reply = True
                            break
                except Exception:
                    pass

            if has_reply:
                ct.dm_status = "replied"
                replied_count += 1
                status_badge = "[bold green]Accepted + Replied![/bold green]"
                console.print(
                    f"  [cyan][{idx}][/cyan] {ct.name} — [bold green]ACCEPTED + REPLIED[/bold green] "
                    f"(operator action needed)"
                )
            else:
                ct.dm_status = "day7_queued"
                accepted_count += 1
                status_badge = "[green]Accepted → Day 7 queue[/green]"
                console.print(f"  [cyan][{idx}][/cyan] {ct.name} — [green]ACCEPTED[/green] → Day 7 queue")

            # Update Bitable
            try:
                fields = bitable._contact_to_fields(ct)
                bitable.update_record(bitable.contacts_table_id, record_id, fields)
            except Exception as exc:
                console.print(f"    [red]Bitable update failed: {exc}[/red]")

            result_table.add_row(ct.name[:20], ct.company_name[:18], sent_date_str, str(days_waiting), status_badge)

        elif days_waiting >= withdraw_after_days:
            # ---- WITHDRAW ----
            withdrawn_ok = False
            inv_id = unipile.find_invitation_for(pid)
            if inv_id:
                withdrawn_ok = unipile.withdraw_invitation(inv_id)

            if withdrawn_ok:
                ct.dm_status = "not_started"
                ct.next_touch_date = now + timedelta(days=30)
                ct.reply_summary = (
                    f"{ct.reply_summary}; " if ct.reply_summary else ""
                ) + f"Connection request withdrawn after {days_waiting} days. Retry later."
                withdrawn_count += 1
                status_badge = f"[red]Withdrawn ({days_waiting}d)[/red]"
                console.print(
                    f"  [cyan][{idx}][/cyan] {ct.name} — [red]WITHDRAWN[/red] "
                    f"after {days_waiting} days (retry in 30 days)"
                )
            else:
                # Couldn't find/withdraw invitation — still mark for retry
                ct.dm_status = "not_started"
                ct.next_touch_date = now + timedelta(days=30)
                ct.reply_summary = (
                    f"{ct.reply_summary}; " if ct.reply_summary else ""
                ) + f"Withdrawal attempted after {days_waiting} days (invitation not found). Retry later."
                withdrawn_count += 1
                status_badge = f"[red]Reset ({days_waiting}d)[/red]"
                console.print(
                    f"  [cyan][{idx}][/cyan] {ct.name} — [red]RESET[/red] "
                    f"after {days_waiting} days (invitation not found, retry in 30 days)"
                )

            try:
                fields = bitable._contact_to_fields(ct)
                bitable.update_record(bitable.contacts_table_id, record_id, fields)
            except Exception as exc:
                console.print(f"    [red]Bitable update failed: {exc}[/red]")

            result_table.add_row(ct.name[:20], ct.company_name[:18], sent_date_str, str(days_waiting), status_badge)

        else:
            # ---- WAITING ----
            waiting_count += 1
            waiting_days.append(days_waiting)
            status_badge = f"[yellow]Waiting ({days_waiting}d)[/yellow]"
            console.print(f"  [cyan][{idx}][/cyan] {ct.name} — [yellow]waiting[/yellow] ({days_waiting} days)")

            result_table.add_row(ct.name[:20], ct.company_name[:18], sent_date_str, str(days_waiting), status_badge)

    # ---- 3. Summary ----
    console.print()
    console.print(result_table)

    avg_waiting = sum(waiting_days) // len(waiting_days) if waiting_days else 0
    console.print(f"\n[bold]Checked {len(contacts)} pending connections:[/bold]")
    console.print(f"  [green]Accepted: {accepted_count}[/green] (moved to Day 7 queue)")
    if replied_count:
        console.print(f"  [bold green]Replied: {replied_count}[/bold green] (operator action needed!)")
    console.print(f"  [yellow]Waiting: {waiting_count}[/yellow] (avg {avg_waiting} days)")
    console.print(f"  [red]Withdrawn: {withdrawn_count}[/red] (will retry in 30 days)")


# ---------------------------------------------------------------------------
# pipeline scores
# ---------------------------------------------------------------------------


@pipeline_app.command("scores")
def pipeline_scores(
    top_companies: int = typer.Option(20, help="Number of top companies to show"),
    top_contacts: int = typer.Option(30, help="Number of top contacts to show"),
) -> None:
    """Display lead score leaderboard for companies and contacts."""
    from src.bitable_client import BitableClient
    from src.lead_scorer import (
        batch_score_companies,
        batch_score_contacts,
        get_company_breakdown,
        get_contact_breakdown,
        score_company as _score_company,
    )
    from src.models import Company, Contact

    settings = load_settings()
    feishu_ready = all([
        settings.feishu_app_id,
        settings.feishu_app_secret,
        settings.feishu_bitable_app_token,
        settings.feishu_companies_table_id,
    ])
    if not feishu_ready:
        console.print("[red]Feishu Bitable credentials not fully configured. Aborting.[/red]")
        raise typer.Exit(1)

    bitable = BitableClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        app_token=settings.feishu_bitable_app_token,
        companies_table_id=settings.feishu_companies_table_id,
        contacts_table_id=settings.feishu_contacts_table_id,
    )

    # ---- Fetch all companies ----
    console.print("[bold]Loading companies...[/bold]")
    raw_companies = bitable.list_records(bitable.companies_table_id)
    companies: list[Company] = []
    for rec in raw_companies:
        try:
            companies.append(bitable._fields_to_company(rec.get("fields", {})))
        except Exception:
            pass

    if not companies:
        console.print("[yellow]No companies found in Bitable.[/yellow]")
        raise typer.Exit(0)

    scored_companies = batch_score_companies(companies)

    # ---- Companies table ----
    co_table = Table(title=f"Top {top_companies} Companies by Lead Score")
    co_table.add_column("#", style="dim", width=3)
    co_table.add_column("公司名", style="cyan", max_width=28)
    co_table.add_column("行业", max_width=18)
    co_table.add_column("员工", justify="right", width=6)
    co_table.add_column("SAP", justify="center", width=5)
    co_table.add_column("Concur", justify="center", width=7)
    co_table.add_column("Score", justify="center", width=5)
    co_table.add_column("Breakdown", style="dim", max_width=50)

    for rank, (comp, score) in enumerate(scored_companies[:top_companies], 1):
        breakdown = get_company_breakdown(comp)
        bd_str = "; ".join(f"{k} +{v}" for k, v in breakdown.items()) if breakdown else "-"
        sap = "[green]Y[/green]" if comp.sap_user == "Yes" else "[dim]?[/dim]" if comp.sap_user == "Unknown" else "N"
        concur = "[green]Y[/green]" if comp.uses_concur == "Yes" else "[dim]?[/dim]" if comp.uses_concur == "Unknown" else "N"
        sc = "green" if score >= 50 else "yellow" if score >= 25 else "dim"

        co_table.add_row(
            str(rank),
            comp.company_name[:28],
            comp.industry,
            str(comp.employee_count),
            sap, concur,
            f"[{sc}]{score}[/{sc}]",
            bd_str,
        )

    console.print()
    console.print(co_table)

    # ---- Fetch all contacts ----
    console.print("\n[bold]Loading contacts...[/bold]")
    raw_contacts = bitable.list_records(bitable.contacts_table_id)
    contacts: list[Contact] = []
    for rec in raw_contacts:
        try:
            contacts.append(bitable._fields_to_contact(rec.get("fields", {})))
        except Exception:
            pass

    if not contacts:
        console.print("[yellow]No contacts found in Bitable.[/yellow]")
        return

    # Build company lookup
    companies_map: dict[str, Company] = {c.company_name: c for c in companies}
    scored_contacts = batch_score_contacts(contacts, companies_map)

    # ---- Contacts table ----
    ct_table = Table(title=f"Top {top_contacts} Contacts by Lead Score")
    ct_table.add_column("#", style="dim", width=3)
    ct_table.add_column("姓名", style="cyan", max_width=18)
    ct_table.add_column("职位", max_width=22)
    ct_table.add_column("公司", max_width=20)
    ct_table.add_column("类型", max_width=12)
    ct_table.add_column("Flow", width=10)
    ct_table.add_column("Score", justify="center", width=5)
    ct_table.add_column("Breakdown", style="dim", max_width=50)

    for rank, (ct, score) in enumerate(scored_contacts[:top_contacts], 1):
        comp = companies_map.get(ct.company_name, Company(
            company_name=ct.company_name, linkedin_url="", industry="Other",
        ))
        breakdown = get_contact_breakdown(ct, comp)
        bd_str = "; ".join(f"{k} +{v}" for k, v in breakdown.items()) if breakdown else "-"
        flow_badge = "[green]reactivate[/green]" if ct.flow_type == "re_activation" else "[dim]cold[/dim]"
        sc = "green" if score >= 50 else "yellow" if score >= 25 else "dim"

        ct_table.add_row(
            str(rank),
            ct.name[:18],
            ct.title[:22],
            ct.company_name[:20],
            ct.contact_type[:12],
            flow_badge,
            f"[{sc}]{score}[/{sc}]",
            bd_str,
        )

    console.print()
    console.print(ct_table)

    # ---- Summary stats ----
    avg_co = sum(s for _, s in scored_companies) // len(scored_companies) if scored_companies else 0
    avg_ct = sum(s for _, s in scored_contacts) // len(scored_contacts) if scored_contacts else 0
    high_co = sum(1 for _, s in scored_companies if s >= 50)
    high_ct = sum(1 for _, s in scored_contacts if s >= 50)

    console.print(
        f"\n[bold]Companies:[/bold] {len(companies)} total, "
        f"avg score {avg_co}, {high_co} high-value (≥50)"
    )
    console.print(
        f"[bold]Contacts:[/bold] {len(contacts)} total, "
        f"avg score {avg_ct}, {high_ct} high-value (≥50)"
    )


# Allow `python -m src.cli <command>` invocation
if __name__ == "__main__":
    app()
