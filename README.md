# linkedin-abm-agent

LinkedIn ABM (Account-Based Marketing) lead generation CLI agent.

## Setup

```bash
# Python 3.11+
pip install -e ".[dev]"

# Copy and fill in environment variables
cp .env.example .env
```

## Usage

```bash
# Check environment configuration
python -m src.cli init

# Or use the installed CLI entry point
abm pipeline init
```

## Project Structure

```
src/
├── cli.py              # Typer CLI entry point
├── config.py           # Environment config via pydantic-settings
├── models.py           # Core Pydantic models (Company, Contact)
├── bitable_client.py   # Feishu Bitable CRUD
├── apify_client.py     # Apify integration
├── unipile_client.py   # Unipile API client
├── enrichment.py       # Data enrichment
├── dm_generator.py     # DM generation via Claude
├── daily_actions.py    # Daily action reports
├── lead_scorer.py      # Lead scoring
└── rate_limiter.py     # API rate limiter
```
