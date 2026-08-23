# Internship Watch + Outreach Pipeline (Tavily Edition)

A Computer Engineering internship monitor that scans public company career pages, filters for target-cycle CE roles, deduplicates them in SQLite, and optionally enriches new matches with public LinkedIn leads via Tavily. It can send Discord alerts and keep a durable networking queue for follow-up.

## What this project does today

- Polls enabled company career sources such as Workday, Eightfold, Amazon, Apple, Lever, Greenhouse, and JSON-LD generic pages.
- Normalizes each posting into a single Job schema.
- Scores and filters postings for CE relevance using `src/filtering.py`.
- Uses a target-year filter and a US-only gate by default.
- Stores active jobs and alert/networking state in SQLite so the pipeline does not spam on reruns.
- Queues new qualifying jobs for Discord alerts and networking enrichment.
- Uses Tavily only for public LinkedIn discovery; it does not log into LinkedIn or automate a browser session.
- Supports commissioning-only source checks, bootstrap alerts, and deep enrichment for a single job.

## Important LinkedIn note

This project does not log into LinkedIn, scrape private pages, or automate LinkedIn activity. It searches the public web index for public LinkedIn URLs and gives you those links for manual review and outreach.

## Current architecture

```text
Company career source
        |
        v
   source adapter
        |
        v
  normalize job
        |
        v
  CE relevance + target-year + US filter
        |
        v
  SQLite dedupe + eligibility state
        |
   new eligible jobs
        |----------------------|
        v                      v
 Discord alert queue      networking queue
        |                      |
        v                      v
  Tavily public LinkedIn   public LinkedIn lead ranking
      search / cache            |
                             v
                    Discord follow-ups if needed
```

## Local setup

### 1) Install dependencies

```bash
cd /path/to/internship_pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2) Configure environment variables

Open `.env` and set at least:

```env
TAVILY_API_KEY=tvly-your-real-key-here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-real-webhook
DB_PATH=data/jobs.db
```

The project also supports the following controls:

```env
TAVILY_AUTO_SEARCH_KINDS=recruiter
TAVILY_MAX_QUERIES=3
TAVILY_MAX_CREDITS_PER_RUN=10
TAVILY_DAILY_CREDIT_CAP=20
TAVILY_CREDIT_RESERVE=250
TAVILY_REQUIRE_USAGE_CHECK=true
MAX_ALERTS_PER_RUN=20
MAX_NETWORKING_JOBS_PER_RUN=20
```

Do not commit `.env` to GitHub.

## Supported source configuration

The active roster lives in `config/companies.yaml`.

### Current active companies

The project currently enables a production-safe set of sources with `target_year: 2027` and `us_only: true`:

```yaml
companies:
  - name: NVIDIA
    enabled: true
    type: workday
    host: nvidia.wd5.myworkdayjobs.com
    tenant: nvidia
    site: NVIDIAExternalCareerSite
    search_text: intern
    target_year: 2027
    us_only: true

  - name: Qualcomm
    enabled: true
    type: eightfold
    board_url: https://careers.qualcomm.com/careers
    domain: qualcomm.com
    query: intern
    max_pages: 20
    target_year: 2027
    us_only: true

  - name: Amazon
    enabled: true
    type: amazon
    query: intern
    country: USA
    result_limit: 100
    max_pages: 20
    page_delay: 0.10
    target_year: 2027
    us_only: true

  - name: Apple
    enabled: true
    type: apple
    query: intern
    locale: en-us
    location_filter: postLocation-USA
    team: STDNT
    sub_team: INTRN
    max_pages: 30
    target_year: 2027
    us_only: true
```

Additional values used by the pipeline include:

- `type`: supported source adapter type (`workday`, `eightfold`, `amazon`, `apple`, `lever`, `greenhouse`, `generic`)
- `target_year`: target internship cycle such as `2027`
- `us_only`: whether the pipeline requires a US location before alerting or Tavily enrichment
- `resolve_ambiguous_relevant`: used by Workday and related adapters for ambiguous-location handling
- `staged_companies`: companies deliberately kept off the live roster until a stable adapter is validated

## Common CLI commands

### Pre-flight validation

Validates that config, API keys, and DB state look healthy without sending Tavily Search or Discord requests.

```bash
python -m src.main --preflight
```

### Commission a source without side effects

Fetches enabled source adapters with no DB writes, no Tavily spends, and no Discord messages.

```bash
python -m src.main --roster-check
python -m src.main --roster-check --company "NVIDIA"
```

### Seed the database

Stores current matches as a baseline without alerting on all of them.

```bash
python -m src.main --seed
```

### Normal run

This is the main production loop.

```bash
python -m src.main
```

### Disable enrichment but keep scanning

Useful for test/debug runs when only the source scan is needed.

```bash
python -m src.main --no-enrich
```

### Deep enrichment for a single stored job

Runs an explicit paid job-specific search for a known active posting. `--include-uf` adds the UF-engineer query.

```bash
python -m src.main --deep-enrich "NVIDIA" "REQ123456"
python -m src.main --deep-enrich "NVIDIA" "REQ123456" --include-uf
```

### Bootstrap Discord alerts without Tavily

Useful when you want to send current active qualifying roles once without any LinkedIn enrichment.

```bash
python -m src.main --bootstrap-alerts
```

### Bootstrap networking digests for already alerted jobs

This does a one-time recruiter-only enrichment digest for current qualifying alerted jobs.

```bash
python -m src.main --bootstrap-networking
```

## Tavily budget and search behavior

This project is intentionally conservative with Tavily usage.

- The default auto-enrichment path is recruiter-only: `TAVILY_AUTO_SEARCH_KINDS=recruiter`
- `exact_post` and `uf_engineer` are opt-in and are reached via `--deep-enrich`
- `TAVILY_MAX_CREDITS_PER_RUN` controls the maximum per-run search budget
- `TAVILY_DAILY_CREDIT_CAP` is the local rolling-24h cap enforced by the SQLite ledger
- `TAVILY_CREDIT_RESERVE` keeps a protected reserve before a new search is allowed
- `TAVILY_REQUIRE_USAGE_CHECK=true` enforces a fail-closed usage check before search requests
- Search queries are constrained to LinkedIn domains; the project does not use broad public-web exploration

The code runs a usage guard before search requests and fails closed if the budget or usage data is unavailable or inconsistent.

## How the filter works

The Python filter in `src/filtering.py` looks for Computer Engineering and internship-relevant terms such as:

- ASIC / FPGA / RTL / Verilog / SystemVerilog / VHDL
- firmware / embedded / SoC / silicon / digital design / verification
- GPU / CUDA / compiler / architecture
- C/C++ / Linux / systems
- engineering-heavy internship language

The filter also respects target-year matching and penalizes senior/staff/manager-heavy titles.

## Running tests

The project uses pytest for the current test suite.

```bash
python -m pytest -q
```

The legacy unittest command also still works:

```bash
python -m unittest discover -s tests -v
```

## GitHub Actions deployment

The pipeline is designed for hourly scheduled GitHub Actions runs.

### Required repo secrets

Add these in GitHub:

- `TAVILY_API_KEY`
- `DISCORD_WEBHOOK_URL`

### Workflow behavior

- Scheduled cadence: hourly at minute 17
- First cloud run auto-seeds if no state database exists, preventing a flood of alerts
- The workflow persists `jobs.db` on a branch such as `internship-state` so the bot remembers which jobs were already seen
- The default cloud path is still `data/jobs.db` unless overridden with `DB_PATH`

## Maintenance notes

- If a company ATS adapter breaks, run `--roster-check` before touching persistent state.
- If a job is not US-eligible, it is blocked before alerting or Tavily enrichment.
- If the usage endpoint or local ledger fails, the pipeline stays safe by refusing to spend new credits.
- Keep `config/companies.yaml` aligned with the current vendor source and target-year expectations before enabling new companies.

## Project status

This README reflects the current v5 codebase and command set. The project is intentionally focused on mission-safe, low-volume public-web internship monitoring rather than broad automation or private platform scraping.
