# Internship Watch + Outreach Pipeline (Tavily Edition)

A Computer Engineering internship monitor that scans public company career pages, filters for target-cycle CE roles, deduplicates them in SQLite, and optionally enriches new matches with public LinkedIn leads via Tavily. It can send Discord alerts and keep a durable networking queue for follow-up.

## What this project does today

- Polls enabled company career sources through Workday, Eightfold, SmartRecruiters, Greenhouse, Amazon, Apple, Google, Lever, and JSON-LD adapters.
- Normalizes each posting into a single Job schema.
- Classifies internship eligibility before any technical-fit decision.
- Scores eligible postings independently for hardware, SWE, and data/ML relevance using `src/filtering.py`.
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
 internship / target-cycle classification
        |
        v
 candidate eligibility
        |
        v
 hardware / SWE / data-ML sector scores
        |
        v
 US-location filter
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
TAVILY_AUTO_SEARCH_KINDS=exact_post,recruiter
TAVILY_AUTO_AUTHOR_LOOKUP=true
TAVILY_MAX_AUTHOR_LOOKUPS_PER_JOB=2
TAVILY_MAX_QUERIES=2
TAVILY_MAX_CREDITS_PER_RUN=20
TAVILY_DAILY_CREDIT_CAP=60
TAVILY_CREDIT_RESERVE=100
TAVILY_REQUIRE_USAGE_CHECK=true
TAVILY_MAX_LEADS_PER_JOB=14
MAX_ALERTS_PER_RUN=20
MAX_NETWORKING_JOBS_PER_RUN=30
EXPECTED_GRAD_YEAR=2029
CANDIDATE_SPECIAL_PROGRAM_ELIGIBLE=false
```

Do not commit `.env` to GitHub.

## Supported source configuration

The active roster lives in `config/companies.yaml`.

### Current active companies

The project currently enables 38 production-safe sources with `target_year: 2027` and `us_only: true`. Tier is employer metadata only; it never changes a job's technical fit score.

| Company | Tier | Provider | Hardware | SWE | Data/ML | Status |
|---|---:|---|:---:|:---:|:---:|---|
| NVIDIA | A | Workday | ✓ | ✓ | ✓ | Active |
| Qualcomm | A | Eightfold | ✓ | ✓ | ✓ | Active |
| Intel | A | Workday | ✓ | ✓ | ✓ | Active |
| Broadcom | A | Workday | ✓ | ✓ |  | Active |
| Analog Devices | B | Workday | ✓ | ✓ |  | Active |
| Cadence | B | Workday | ✓ | ✓ |  | Active |
| Marvell | B | Workday | ✓ | ✓ |  | Active |
| Micron | A | Eightfold | ✓ | ✓ | ✓ | Active |
| NXP | B | Workday | ✓ | ✓ |  | Active |
| Silicon Labs | B | Workday | ✓ | ✓ |  | Active |
| Microsoft | A | Eightfold | ✓ | ✓ | ✓ | Active |
| Amazon | A | Amazon Jobs API | ✓ | ✓ | ✓ | Active |
| Apple | A | Apple careers API | ✓ | ✓ | ✓ | Active |
| Google | A | Google careers payload | ✓ | ✓ | ✓ | Active |
| Cloudflare | B | Greenhouse |  | ✓ | ✓ | Active |
| Datadog | B | Greenhouse |  | ✓ | ✓ | Active |
| MongoDB | B | Greenhouse |  | ✓ | ✓ | Active |
| ServiceNow | B | SmartRecruiters |  | ✓ | ✓ | Active |
| Bosch | C | SmartRecruiters | ✓ | ✓ | ✓ | Active |
| Databricks | A | Greenhouse |  | ✓ | ✓ | Active |
| Stripe | A | Greenhouse |  | ✓ | ✓ | Active |
| Palantir | A | Lever |  | ✓ | ✓ | Active |
| Applied Materials | B | Workday | ✓ | ✓ | ✓ | Active |
| KLA | B | Workday | ✓ | ✓ | ✓ | Active |
| Lam Research | B | Eightfold | ✓ | ✓ | ✓ | Active |
| GlobalFoundries | B | Eightfold | ✓ | ✓ |  | Active |
| Microchip | B | Workday | ✓ | ✓ |  | Active |
| Roblox | B | Greenhouse |  | ✓ | ✓ | Active |
| Autodesk | B | Workday | ✓ | ✓ | ✓ | Active |
| Northrop Grumman | C | Workday | ✓ | ✓ | ✓ | Active |
| RTX | C | Workday | ✓ | ✓ | ✓ | Active |
| Capital One | C | Workday |  | ✓ | ✓ | Active |
| Mastercard | C | Workday |  | ✓ | ✓ | Active |
| HubSpot | C | Greenhouse |  | ✓ | ✓ | Active |
| Cox Enterprises | C | Workday |  | ✓ | ✓ | Active |
| Home Depot | C | Workday |  | ✓ | ✓ | Active |
| Fiserv | C | Workday |  | ✓ | ✓ | Active |
| Equifax | C | Workday |  | ✓ | ✓ | Active |

Additional values used by the pipeline include:

- `type`: supported source adapter type (`workday`, `eightfold`, `smartrecruiters`, `greenhouse`, `amazon`, `apple`, `google`, `lever`, `generic`)
- `company_tier`: `A`, `B`, or `C`; ranking metadata that is intentionally separate from fit
- `priority_sectors`: one or more of `hardware`, `swe`, and `data_ml`
- `target_year`: target internship cycle such as `2027`
- `us_only`: whether the pipeline requires a US location before alerting or Tavily enrichment
- `title_terms`: provider-side/local opportunity terms used to avoid detail calls for unrelated jobs on large boards
- `facet_terms`: Workday facet descriptors resolved to provider IDs on every scan; used to restrict large boards to internship/student job types before pagination
- `max_pages` / `max_detail_resolutions`: fail-safe capacity controls; exceeding either aborts the company sync rather than recording a partial snapshot
- `resolve_ambiguous_relevant`: used by Workday and related adapters for ambiguous-location handling
- `staged_companies`: companies deliberately kept off the live roster until a stable adapter is validated

### Adding another company

Prefer an existing provider adapter and add one declarative company entry. For Workday, supply `host`, `tenant`, and `site`; for Greenhouse, supply `board_token`; for SmartRecruiters, supply the public career-site `identifier`. Always add tier/sector metadata, keep `target_year` and `us_only` explicit, add or reuse a provider fixture, and commission with:

```bash
python -m src.main --roster-check --company "Company Name"
```

The roster check distinguishes a healthy zero-match day from a parser failure by reporting provider row counts, title candidates, detail lookups, and hard failures. It never writes the DB or calls Discord/Tavily.

Do not enable a source that needs CAPTCHA bypass, authenticated endpoints, or brittle browser automation. Keep it under `staged_companies` with the concrete technical reason instead.

The remaining requested targets stay in `staged_companies` with a concrete reason. Major groups include custom or uncommissioned career systems (AMD, Arm, Meta, Snowflake, Bloomberg, Synopsys, Texas Instruments, Tesla, Intuit, and MathWorks), former Workday URLs that no longer expose the expected public CXS endpoint, and healthy Workday boards such as Motorola Solutions and Rockwell Automation that currently lack a rate-safe internship facet. They are deliberately not treated as supported until a source can distinguish a healthy zero-internship day from an incomplete or excessively broad scan.

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

This project uses Tavily liberally for qualified jobs while retaining hard usage safety.

- The default path runs `exact_post` first and then the reusable `recruiter` search.
- Exact-post searches use company, title, and requisition ID and request up to 10 LinkedIn posts for one basic-search credit.
- When post authors can be extracted confidently, at most two cached author-profile lookups run per job by default.
- A fully uncached job therefore costs at most four searches: exact post, two author profiles, and recruiter; cache hits reduce that cost.
- Author profiles are distinguished as recruiter, technical connection, or potential connection; the original post remains linked.
- University of Florida and Pi Kappa Alpha evidence is tagged as `UF` and `PIKE` and receives a ranking bonus.
- Company/role recruiter and author-profile searches are cached for 14 days; exact-post searches are cached for 18 hours.
- `uf_engineer` remains opt-in through `--deep-enrich --include-uf`.
- `TAVILY_MAX_CREDITS_PER_RUN` controls the maximum per-run search budget
- `TAVILY_DAILY_CREDIT_CAP` is the local rolling-24h cap enforced by the SQLite ledger
- `TAVILY_CREDIT_RESERVE` keeps a protected reserve before a new search is allowed
- `TAVILY_REQUIRE_USAGE_CHECK=true` enforces a fail-closed usage check before search requests
- Balanced per-kind quotas prevent job posts from crowding recruiter/profile results out of alerts.
- Search queries are constrained to LinkedIn domains; the project does not use broad public-web exploration

The code runs a usage guard before search requests and fails closed if the budget or usage data is unavailable or inconsistent.

## Eligibility and sector scoring

`src/filtering.py` evaluates the pipeline in this order: normalize, classify the opportunity, evaluate candidate eligibility, enforce the target cycle, classify sectors, then compute technical fit. The title itself must identify an internship; mentions such as “interview interns,” “train interns,” `internal`, or `student` in a regular job description cannot rescue a non-intern title. `Graduate Intern` is a hard rejection while `Undergraduate Intern` remains valid.

For companies configured with `target_year: 2027`, alerts fail closed unless the posting explicitly identifies the 2027 internship cycle. Summer 2027 titles, descriptions, and company-wide `2027 Internships` pools qualify. Winter, spring, fall, co-op, year-round, academic-year, six-month, wrong-year, and undated internships do not. Workday resolves technically relevant internship details when list rows omit cycle or degree evidence, so strict filtering does not depend solely on the abbreviated listing payload.

Explicit graduate-only, non-intern/new-grad, restricted special-program, and incompatible graduation-window postings are ineligible before sector terms can score. Missing degree information on an otherwise proven Summer 2027 internship remains uncertain instead of becoming a fabricated rejection.

Candidate configuration defaults to an expected 2029 graduation and no SkillBridge/returnship eligibility. Override those defaults with `EXPECTED_GRAD_YEAR` and `CANDIDATE_SPECIAL_PROGRAM_ELIGIBLE`.

Eligible and uncertain postings receive independent `hardware`, `swe`, and `data_ml` scores. Overall compatibility uses the strongest applicable sector instead of averaging all three, so specialized hardware or data-science internships are not diluted. Company tier is never an input to these scores.

High-value crossover signals—systems software, CUDA/GPU, compilers, ML infrastructure, embedded AI, and performance engineering—can score in multiple sectors.

## Provider rate and snapshot behavior

- Workday resolves details only for relevant postings whose list-view location is ambiguous.
- Greenhouse downloads a compact board once, then resolves only token-matched internship/student titles.
- SmartRecruiters uses documented offset pagination plus its country filter, then applies the same token-aware title filter because some live tenants ignore the public `q` parameter.
- Eightfold repairs unstable page-boundary duplicates and refuses incomplete snapshots.
- Capacity limits fail the company safely; partial results are never synced as if jobs disappeared.

None of these listing/detail calls use Tavily. Eligibility and US-location gates still run before any queued external enrichment.

## Alert contents

Qualifying Discord and console alerts show the structured eligibility decision, a verification reason when information is uncertain, the primary track, recommended resume, overall fit, and all three sector scores. For example:

```text
Eligibility: ✅ Eligible
Primary track: SWE / Systems
Recommended resume: SWE
Overall fit: 89
Fit by track
Hardware / Architecture: 42
SWE / Systems: 89
Data / ML / AI: 65
```

Hard-ineligible postings never enter either the alert or networking queue. Uncertain postings can alert when their technical relevance and location pass, but are visibly marked `⚠️ Verify` with the missing requirement noted.

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

This README reflects the Phase 3 provider/config expansion. The project remains focused on mission-safe, low-volume public-web internship monitoring rather than broad automation or private platform scraping.
