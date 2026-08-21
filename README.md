# Internship Watch + Outreach Pipeline (Tavily Edition)

A personal internship intelligence pipeline for a Computer Engineering student. This version uses **Tavily** for public-web discovery instead of Brave Search.

## What it does

1. Polls company career systems (Workday, Lever, Greenhouse, plus a JSON-LD fallback).
2. Normalizes jobs into one schema.
3. Scores postings for internship + Computer Engineering fit.
4. Uses SQLite to identify genuinely new postings instead of repeatedly alerting you.
5. For each new match, optionally uses Tavily to find publicly indexed LinkedIn posts/profiles related to the role.
6. Ranks possible outreach leads, including recruiters, likely engineers/managers, and UF alumni.
7. Sends a Discord alert with the job and the best leads.
8. Can run automatically every hour with GitHub Actions.

## Important LinkedIn note

This project **does not log into, crawl, or automate LinkedIn**. Tavily searches the public web index for LinkedIn URLs and the bot gives you those links for manual review/outreach.

## Architecture

```text
Company careers
   |-- Workday
   |-- Lever
   |-- Greenhouse
   `-- JSON-LD fallback
            |
            v
       normalize Job
            |
            v
      CE-fit scoring
            |
            v
      SQLite dedupe
            |
       only NEW jobs
        /          \
       v            v
Discord alert   Tavily Search
                    |
                    v
          public LinkedIn URLs
                    |
                    v
             rank outreach leads
                    |
                    v
              Discord alert
```

# Start here

## 1. Create a free Tavily API key

Create an account at Tavily and copy your API key. Tavily currently includes 1,000 free API credits each month with no credit card required.

This project deliberately uses `search_depth="basic"` and only **3 searches per new job** by default, so it stays lightweight. You can change the budget with `TAVILY_MAX_QUERIES`.

## 2. Create a Discord webhook

Create a private Discord channel, then:

**Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**

## 3. Local setup on macOS

Unzip the project, open Terminal, and run:

```bash
cd path/to/internship_pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in:

```env
TAVILY_API_KEY=tvly-your-real-key-here
TAVILY_MAX_QUERIES=3
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your-real-webhook
DB_PATH=data/jobs.db
```

Do **not** commit `.env` to GitHub.

## 4. Seed the database

First run:

```bash
python -m src.main --seed
```

This records internships that already exist without alerting you about all of them.

Then normal runs are:

```bash
python -m src.main
```

Only genuinely new relevant jobs should produce alerts.

## 5. Test without Tavily or Discord

The career-page portion works even if the API keys are blank:

```bash
python -m src.main --no-enrich
```

New jobs will print in Terminal instead of sending a Discord alert if `DISCORD_WEBHOOK_URL` is blank.

## What Tavily searches for

For each newly discovered role, the enrichment stage uses up to three focused searches:

```text
site:linkedin.com/posts "NVIDIA" ("ASIC Design Intern" OR "JR12345")
site:linkedin.com/in "NVIDIA" (recruiter OR "university recruiting" OR "early careers" OR "talent acquisition")
site:linkedin.com/in "NVIDIA" ("University of Florida" OR UF) (engineer OR manager OR hardware OR firmware OR silicon OR verification)
```

Tavily is restricted to `linkedin.com` results for this enrichment stage.

## Configure companies

Edit `config/companies.yaml`.

### Workday

```yaml
- name: NVIDIA
  enabled: true
  type: workday
  host: nvidia.wd5.myworkdayjobs.com
  tenant: nvidia
  site: NVIDIAExternalCareerSite
  search_text: intern
```

### Lever

```yaml
- name: ExampleCo
  enabled: true
  type: lever
  site: exampleco
```

### Greenhouse

```yaml
- name: ExampleAI
  enabled: true
  type: greenhouse
  board_token: exampleai
```

### Generic JSON-LD

```yaml
- name: ExampleHardware
  enabled: true
  type: generic
  url: https://example.com/careers
```

## Tune the Computer Engineering filter

Edit `src/filtering.py`. High-value terms currently include:

- FPGA / ASIC / RTL / Verilog / SystemVerilog / VHDL
- firmware / embedded
- SoC / silicon / digital design / verification
- computer architecture
- GPU / CUDA / compiler
- C/C++ / Linux / systems

Senior/staff/manager roles are penalized.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## GitHub Actions deployment

Once local testing works:

1. Push the repository to GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Add `TAVILY_API_KEY`.
4. Add `DISCORD_WEBHOOK_URL`.
5. Open **Actions → Internship Watch → Run workflow**.
6. Set `seed = true` for that first cloud run.
7. Scheduled runs then execute hourly (at minute 17). The first cloud run auto-seeds if no persisted state exists, preventing an alert storm.

The workflow persists `jobs.db` on an orphan branch called `internship-state`, so GitHub Actions remembers which postings were already seen.

## Next build phase

The next useful layer is a small recruiting dashboard:

`NEW → APPLYING → APPLIED → OUTREACH → RESPONSE → INTERVIEW → CLOSED`

Each job can have a networking panel for:

- employee who posted/shared the role
- university recruiter
- UF alumni
- engineers on the likely team
- possible hiring managers
- outreach/follow-up status
