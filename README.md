# linkedin-abm-agent

CLI-based LinkedIn ABM (Account-Based Marketing) lead generation agent for SAP Concur implementation services. Built for Hitpoint Solution's ANZ + Greater China outreach pipeline.

Automates the full outreach lifecycle: company discovery, contact scraping, data enrichment, lead scoring, personalised DM generation (via Claude), engagement warming, message delivery (via Unipile), and pipeline analytics — all backed by Feishu Bitable as the CRM.

## Setup

### Requirements

- Python 3.11+
- Feishu Bitable tables (Companies + Contacts)
- API keys: Apify, Anthropic, Unipile, Brave Search

### Installation

```bash
pip install -e ".[dev]"
cp .env.example .env
# Fill in all API keys in .env
```

### Environment Variables

| Variable | Service | Purpose |
|---|---|---|
| `APIFY_API_TOKEN` | Apify | LinkedIn company/people scraping |
| `FEISHU_APP_ID` | Feishu | Bitable authentication |
| `FEISHU_APP_SECRET` | Feishu | Bitable authentication |
| `FEISHU_BITABLE_APP_TOKEN` | Feishu | Bitable app identifier |
| `FEISHU_COMPANIES_TABLE_ID` | Feishu | Companies table |
| `FEISHU_CONTACTS_TABLE_ID` | Feishu | Contacts table |
| `ANTHROPIC_API_KEY` | Anthropic | Claude DM generation |
| `UNIPILE_API_KEY` | Unipile | LinkedIn messaging API |
| `UNIPILE_DSN` | Unipile | API base URL |
| `UNIPILE_ACCOUNT_ID` | Unipile | LinkedIn account ID |
| `BRAVE_SEARCH_API_KEY` | Brave | Company enrichment searches |
| `WARMUP_MODE` | Safety | `true` for first 2 weeks (limits to 10 DMs/day) |
| `WARMUP_DAILY_LIMIT` | Safety | Max DMs/day during warmup (default: 10) |
| `MEETING_PIPELINE_VALUE` | Analytics | AUD per meeting for pipeline estimation (default: 50000) |

### Verify Setup

```bash
python -m src.cli pipeline init
```

## Quick Start

From zero to first DM in 6 steps:

```bash
# 1. Scrape target companies
python -m src.cli pipeline scrape-companies \
  --industry "Manufacturing,Mining" \
  --location "Australia" \
  --min-employees 200

# 2. Enrich company data (SAP/Concur signals, news, tech stack)
python -m src.cli pipeline enrich --limit 50

# 3. Import existing LinkedIn connections (optional)
python -m src.cli pipeline import-existing --file Connections.csv

# 4. Scrape finance contacts at top-scoring companies
python -m src.cli pipeline scrape-contacts --min-score 30 --limit 20

# 5. Generate personalised DMs
python -m src.cli pipeline generate-dms --touch day1

# 6. Warm up, then send
python -m src.cli pipeline warm --touch day1
# (wait 2-4 hours)
python -m src.cli pipeline push-dms --touch day1 --confirm-all
```

## Daily Workflow

Run these commands daily (in order):

```bash
# 1. Check if cold_new connection requests were accepted
python -m src.cli pipeline check-acceptances

# 2. View today's dashboard
python -m src.cli pipeline daily

# 3. Generate DMs for contacts due today
python -m src.cli pipeline generate-dms --touch day7
python -m src.cli pipeline generate-dms --touch day14
python -m src.cli pipeline generate-dms --touch day21

# 4. Warm engagement (profile views + post likes)
python -m src.cli pipeline warm --touch day7

# 5. Send DMs (wait 2-4 hours after warming)
python -m src.cli pipeline push-dms --touch day7
python -m src.cli pipeline push-dms --touch day14
python -m src.cli pipeline push-dms --touch day21

# 6. Mark any manual outcomes
python -m src.cli pipeline mark replied --contact "Jane Smith" --summary "Wants to chat"
python -m src.cli pipeline mark meeting --contact "Jane Smith"

# 7. Review stats
python -m src.cli pipeline stats
```

## All Commands

### Pipeline Setup

| Command | Description |
|---|---|
| `pipeline init` | Verify env vars and test Bitable connection |
| `pipeline scrape-companies` | Scrape LinkedIn companies via Apify |
| `pipeline import-existing --file CSV` | Import existing LinkedIn connections |

### Data Enrichment

| Command | Description |
|---|---|
| `pipeline enrich` | Enrich companies via Brave Search (SAP/Concur, news, tech stack) |
| `pipeline scrape-contacts` | Find finance contacts at target companies |
| `pipeline scores` | Display lead score leaderboard |

### DM Lifecycle

| Command | Description |
|---|---|
| `pipeline generate-dms --touch dayN` | Generate DM drafts via Claude |
| `pipeline warm --touch dayN` | Profile view + post like before sending |
| `pipeline push-dms --touch dayN` | Send queued DMs via Unipile |
| `pipeline check-acceptances` | Check cold_new connection request status |

### Manual Status Updates

| Command | Description |
|---|---|
| `pipeline mark sent --contact NAME --touch dayN` | Mark DM as manually sent |
| `pipeline mark replied --contact NAME --summary "..."` | Record a reply |
| `pipeline mark rejected --contact NAME` | Mark contact as opted out |
| `pipeline mark meeting --contact NAME` | Record a booked meeting |

### Reporting

| Command | Description |
|---|---|
| `pipeline daily` | Daily dashboard with workflow suggestions |
| `pipeline daily --week` | 7-day forward plan |
| `pipeline stats` | Full pipeline analytics and funnel metrics |
| `pipeline stats --period week` | Stats filtered to last 7 days |

## DM Sequence

Two flows with 4 touches each, spaced 7 days apart:

### Re-activation (existing connections)

| Touch | Strategy | Max Length |
|---|---|---|
| Day 1 | Warm reconnect + soft CTA | 4 sentences |
| Day 7 | Share industry insight | 300 chars |
| Day 14 | Free value offer (Health Check / guide) | 300 chars |
| Day 21 | Direct meeting ask + Calendly link | 4 sentences |

### Cold New (new connections)

| Touch | Strategy | Max Length |
|---|---|---|
| Day 1 | Connection request note | 300 chars |
| Day 7 | Compliance tip (after acceptance) | 300 chars |
| Day 14 | Benchmark / guide delivery | 300 chars |
| Day 21 | Final touch + Calendly link | 4 sentences |

## Safety

- **Daily send limit**: 80 DMs/day (configurable)
- **Warmup mode**: 10 DMs/day for first 2 weeks (`WARMUP_MODE=true`)
- **Human-like delays**: 20-40s between sends, 5-10s between warming actions
- **429 protection**: Immediately stops on Unipile rate limit
- **Auto-withdrawal**: Connection requests withdrawn after 14 days (configurable)
- **Interactive review**: Per-message confirmation before sending (skip with `--confirm-all`)

## Project Structure

```
src/
├── cli.py              # Typer CLI (13 commands + 4 mark subcommands)
├── config.py           # pydantic-settings env config
├── models.py           # Pydantic models (Company, Contact)
├── bitable_client.py   # Feishu Bitable CRUD with retry + rate limiting
├── apify_client.py     # Apify LinkedIn scraping (companies + contacts)
├── unipile_client.py   # Unipile API (messaging, warming, invitations)
├── enrichment.py       # Brave Search enrichment (5-step pipeline)
├── dm_generator.py     # Claude-powered DM generation with quality validation
├── lead_scorer.py      # Weighted signal scoring (0-100)
├── daily_actions.py    # Daily reports and pipeline statistics
└── rate_limiter.py     # Sliding-window rate limiter
```

## Development

```bash
# Run tests
python -m pytest tests/ -v

# Lint
ruff check src/ tests/
```
