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


# Allow `python -m src.cli <command>` invocation
if __name__ == "__main__":
    app()
