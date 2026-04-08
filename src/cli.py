"""Typer CLI entry point for linkedin-abm-agent."""

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


# Allow `python -m src.cli <command>` invocation
if __name__ == "__main__":
    app()
