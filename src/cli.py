"""Typer CLI entry point for linkedin-abm-agent."""

import time
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
