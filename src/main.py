import argparse
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.alerts import discord_alert, discord_networking_followup
from src.db import JobDB
from src.enrich import TavilyBudget, build_search_specs, selected_search_specs, search_linkedin_public_index_outcome
from src.filtering import evaluate_eligibility, is_relevant, score_sector_fit
from src.locations import classify_us_location
from src.sources.amazon import AmazonSource
from src.sources.apple import AppleSource
from src.sources.eightfold import EightfoldSource
from src.sources.generic import GenericJsonLdSource
from src.sources.google import GoogleSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.lever import LeverSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.workday import WorkdaySource


ROOT = Path(__file__).resolve().parents[1]


def build_source(cfg):
    t = cfg["type"]
    name = cfg["name"]
    if t == "workday":
        return WorkdaySource(
            name, cfg["host"], cfg["tenant"], cfg["site"], cfg.get("search_text", "intern"),
            cfg.get("target_year"), cfg.get("resolve_ambiguous_relevant", True),
            cfg.get("max_detail_resolutions", 100),
        )
    if t == "amazon":
        return AmazonSource(
            name, cfg.get("query", "intern"), cfg.get("country", "USA"),
            cfg.get("result_limit", 100), cfg.get("max_pages", 20), cfg.get("page_delay", 0.10),
        )
    if t == "apple":
        return AppleSource(
            name, cfg.get("query", "intern"), cfg.get("locale", "en-us"),
            cfg.get("location_filter", "postLocation-USA"), cfg.get("max_pages", 25),
            cfg.get("team", ""), cfg.get("sub_team", ""),
        )
    if t == "eightfold":
        return EightfoldSource(name, cfg["board_url"], cfg.get("domain", ""), cfg.get("query", "intern"), cfg.get("max_pages", 20))
    if t == "google":
        return GoogleSource(name, cfg.get("url") or cfg.get("board_url") or cfg.get("jobs_url"))
    if t == "lever":
        return LeverSource(name, cfg["site"])
    if t == "greenhouse":
        return GreenhouseSource(
            name, cfg["board_token"], cfg.get("title_terms"),
            cfg.get("max_detail_resolutions", 100),
        )
    if t == "smartrecruiters":
        return SmartRecruitersSource(
            name, cfg["identifier"], cfg.get("query", "intern"),
            cfg.get("country", "us"), cfg.get("title_terms"),
            cfg.get("page_size", 100), cfg.get("max_pages", 10),
        )
    if t == "generic":
        return GenericJsonLdSource(name, cfg["url"])
    raise ValueError(f"Unknown source type: {t}")


def _db_path():
    path = os.getenv("DB_PATH", str(ROOT / "data" / "jobs.db"))
    return path if os.path.isabs(path) else str(ROOT / path)


def _eligibility(job, company_cfg: dict | None, threshold: float = 12.0):
    """Return (eligible_for_alert_and_tavily, reason).

    The same guard is used at discovery time *and again* immediately before
    Tavily/Discord. This prevents stale queue rows from ever spending credits or
    sending an international alert after configuration changes.
    """
    if not company_cfg:
        return False, "company config missing; failed closed"

    target_year = company_cfg.get("target_year")
    if not is_relevant(job, threshold, target_year=target_year):
        return False, "not CE/target-cycle relevant"

    if company_cfg.get("us_only", True):
        decision = classify_us_location(job.location or "", company_cfg.get("type", ""))
        if not decision.is_us:
            return False, f"{decision.status}: {decision.reason}"
        return True, decision.reason

    return True, "US-only filter disabled for company"


def preflight(config_path: str):
    """Local validation that never calls Tavily Search or Discord."""
    load_dotenv(ROOT / ".env")
    problems = []

    key = os.getenv("TAVILY_API_KEY", "")
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    print("PRE-FLIGHT (zero Tavily Search credits)")
    print(f"Tavily key present: {'yes' if bool(key) else 'NO'}")
    print(f"Discord webhook present: {'yes' if bool(webhook) else 'NO'}")
    if key and not key.startswith("tvly-"):
        problems.append("TAVILY_API_KEY does not look like a Tavily key")
    if webhook and not webhook.startswith("https://discord.com/api/webhooks/"):
        problems.append("DISCORD_WEBHOOK_URL does not look like a Discord webhook")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    enabled = [c for c in cfg.get("companies", []) if c.get("enabled", True)]
    company_cfg = {c["name"]: c for c in enabled}
    for company in enabled:
        try:
            build_source(company)
        except Exception as e:
            problems.append(f"{company.get('name', '?')}: invalid source config: {e}")
    print(f"Enabled company configs: {len(enabled)}")
    print(f"US-only company configs: {sum(1 for c in enabled if c.get('us_only', True))}")

    db = JobDB(_db_path())
    try:
        print(f"Pending alerts: {db.pending_alert_count()}")
        rows = db.conn.execute(
            """
            SELECT company, external_id, title, location, url, source, posted_at, description, score
            FROM jobs WHERE is_active=1 ORDER BY score DESC
            """
        ).fetchall()

        sample = None
        excluded_non_us = 0
        excluded_ambiguous = 0
        from src.models import Job
        for row in rows:
            job = Job(*row)
            c = company_cfg.get(job.company)
            eligible, reason = _eligibility(job, c, 12.0)
            if eligible and sample is None:
                sample = job
            elif not eligible and (c and c.get("us_only", True)):
                decision = classify_us_location(job.location or "", c.get("type", ""))
                if decision.status == "non_us":
                    excluded_non_us += 1
                elif decision.status == "ambiguous":
                    excluded_ambiguous += 1

        print(f"Active non-US jobs excluded from alert/Tavily path: {excluded_non_us}")
        print(f"Active ambiguous-location jobs failed closed: {excluded_ambiguous}")

        if sample:
            print(f"Sample US-eligible job: {sample.company} — {sample.title} — {sample.location}")
            auto_kinds = {spec.kind for spec in selected_search_specs(sample)}
            for spec in build_search_specs(sample):
                cached = db.get_cached_search(spec.cache_key, spec.ttl_hours) is not None
                mode = "AUTO" if spec.kind in auto_kinds else "MANUAL"
                cost = "CACHE" if cached else "1 credit if executed"
                print(
                    f"  [{spec.kind}] {mode} | {cost} | "
                    f"domains={','.join(spec.include_domains)} | {spec.query}"
                )
        else:
            print("No active US-eligible sample job in DB; query-plan preview skipped.")
    finally:
        db.close()

    print(f"Automatic Tavily search kinds: {os.getenv('TAVILY_AUTO_SEARCH_KINDS', 'exact_post,recruiter')}")
    print(f"Automatic exact-post author lookup: {os.getenv('TAVILY_AUTO_AUTHOR_LOOKUP', 'true')}")
    print(f"Author profile lookups per job: {os.getenv('TAVILY_MAX_AUTHOR_LOOKUPS_PER_JOB', '2')}")
    print(f"Per-run Tavily cap: {os.getenv('TAVILY_MAX_CREDITS_PER_RUN', '20')}")
    print(f"Local rolling-24h Tavily cap: {os.getenv('TAVILY_DAILY_CREDIT_CAP', '60')}")
    print(f"Protected Tavily reserve: {os.getenv('TAVILY_CREDIT_RESERVE', '100')}")
    print("Usage verification before search: " + os.getenv("TAVILY_REQUIRE_USAGE_CHECK", "true"))
    print("Queue revalidation before Tavily/Discord: enabled")

    # GET /usage is a zero-Search-credit account check. Run the exact guard
    # production will use so schema/account-plan surprises are caught here,
    # before a live Search request is ever permitted.
    if key:
        db_for_usage = JobDB(_db_path())
        try:
            local_today = db_for_usage.tavily_credits_used_last_24h()
            usage_budget = TavilyBudget(
                key, local_daily_usage_getter=db_for_usage.tavily_credits_used_last_24h
            )
            if usage_budget.check():
                print(
                    f"Tavily usage guard: PASS via {usage_budget.usage_source}; "
                    f"{usage_budget.remaining_before_search} credits remaining before reserve; "
                    f"{local_today} locally recorded in last 24h"
                )
            else:
                problems.append(f"Tavily usage guard failed safely: {usage_budget.block_reason}")
        finally:
            db_for_usage.close()
    else:
        problems.append("TAVILY_API_KEY is missing")

    if problems:
        print("\nPRE-FLIGHT FAILED:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)
    print("\nPRE-FLIGHT PASSED. No Tavily Search requests were made.")


def roster_check(config_path: str, threshold: float = 12.0, company_name: str | None = None):
    """Fetch configured career sources without DB writes, Discord, or Tavily.

    This is the required commissioning step for a larger company roster. A bad
    ATS adapter can fail here without changing durable job state or consuming
    outreach credits.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    enabled = [c for c in cfg.get("companies", []) if c.get("enabled", True)]
    if company_name:
        enabled = [c for c in enabled if c.get("name", "").lower() == company_name.lower()]
        if not enabled:
            raise SystemExit(f"No enabled company named: {company_name}")

    print("ROSTER CHECK (zero Tavily Search credits, zero Discord, zero DB writes)")
    print(f"Sources to check: {len(enabled)}")
    healthy = 0
    failed = 0
    aggregate_eligibility = {"eligible": 0, "uncertain": 0, "ineligible": 0}
    aggregate_sectors = {"hardware": 0, "swe": 0, "data_ml": 0}
    sector_samples = {}

    for company in enabled:
        name = company["name"]
        try:
            source = build_source(company)
            jobs = source.fetch()
            relevant = []
            eligible = []
            non_us = 0
            ambiguous = 0
            duplicate_keys = len(jobs) - len({(j.company, j.external_id) for j in jobs})
            eligibility_counts = {"eligible": 0, "uncertain": 0, "ineligible": 0}
            sector_counts = {"hardware": 0, "swe": 0, "data_ml": 0}

            for job in jobs:
                candidate_status = evaluate_eligibility(job).status
                eligibility_counts[candidate_status] += 1
                aggregate_eligibility[candidate_status] += 1
                is_fit = is_relevant(job, threshold, target_year=company.get("target_year"))
                if not is_fit:
                    continue
                relevant.append(job)
                scores = score_sector_fit(job)
                primary_sector = max(scores, key=scores.get)
                sector_counts[primary_sector] += 1
                aggregate_sectors[primary_sector] += 1
                ok, _ = _eligibility(job, company, threshold)
                if ok:
                    eligible.append(job)
                    sector_samples.setdefault(primary_sector, job)
                elif company.get("us_only", True):
                    decision = classify_us_location(job.location or "", company.get("type", ""))
                    if decision.status == "non_us":
                        non_us += 1
                    elif decision.status == "ambiguous":
                        ambiguous += 1

            note = getattr(source, "last_scan_note", "")
            extra = f" | {note}" if note else ""
            if ambiguous:
                samples = []
                for job in relevant:
                    decision = classify_us_location(job.location or "", company.get("type", ""))
                    if decision.status == "ambiguous":
                        samples.append(f"{job.title} @ {job.location or '[blank]'}")
                        if len(samples) == 2:
                            break
                if samples:
                    extra += " | ambiguous samples: " + " ; ".join(samples)
            if duplicate_keys:
                extra += f" | WARNING {duplicate_keys} duplicate IDs"
            if len(jobs) > 500:
                extra += f" | WARNING huge source scan ({len(jobs)} rows)"
            print(
                f"OK   {name}: {len(jobs)} fetched | {len(relevant)} relevant | "
                f"{len(eligible)} US-eligible | {non_us} non-US relevant | "
                f"{ambiguous} ambiguous | eligibility "
                f"E/U/I {eligibility_counts['eligible']}/{eligibility_counts['uncertain']}/"
                f"{eligibility_counts['ineligible']} | primary sectors "
                f"H/S/D {sector_counts['hardware']}/{sector_counts['swe']}/"
                f"{sector_counts['data_ml']}{extra}"
            )
            healthy += 1
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e}")

    staged = cfg.get("staged_companies", [])
    print(f"\nHealthy enabled sources: {healthy}/{len(enabled)}")
    print(f"Failed safely: {failed}")
    print(
        "Candidate eligibility totals (eligible/uncertain/ineligible): "
        f"{aggregate_eligibility['eligible']}/{aggregate_eligibility['uncertain']}/"
        f"{aggregate_eligibility['ineligible']}"
    )
    print(
        "Relevant primary-sector totals (hardware/swe/data_ml): "
        f"{aggregate_sectors['hardware']}/{aggregate_sectors['swe']}/"
        f"{aggregate_sectors['data_ml']}"
    )
    for sector in ("hardware", "swe", "data_ml"):
        sample = sector_samples.get(sector)
        if sample:
            print(
                f"Sample {sector}: {sample.company} — {sample.title} — "
                f"{sample.location or 'location not listed'}"
            )
    if staged and not company_name:
        print(f"Staged/not yet enabled: {len(staged)}")
        for item in staged:
            print(f"  - {item.get('name')}: {item.get('reason', 'adapter not commissioned yet')}")
    print("No database rows, Discord messages, or Tavily Search requests were created.")
    if failed:
        raise SystemExit(3)


def run(config_path: str, threshold: float, enrich: bool, seed: bool = False):
    load_dotenv(ROOT / ".env")
    db = JobDB(_db_path())
    synced_companies = 0
    failed_companies = 0
    all_alert_candidates = []

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        enabled_configs = [c for c in cfg.get("companies", []) if c.get("enabled", True)]
        config_by_company = {c["name"]: c for c in enabled_configs}

        for company in enabled_configs:
            name = company["name"]
            try:
                source = build_source(company)
                jobs = source.fetch()

                relevant_keys = set()
                eligible_keys = set()
                reasons = {}
                non_us_relevant = 0
                ambiguous_relevant = 0

                for job in jobs:
                    target_year = company.get("target_year")
                    relevant = is_relevant(job, threshold, target_year=target_year)
                    key = (job.company, job.external_id)
                    if relevant:
                        relevant_keys.add(key)

                    eligible, reason = _eligibility(job, company, threshold)
                    reasons[key] = reason
                    if eligible:
                        eligible_keys.add(key)
                    elif relevant and company.get("us_only", True):
                        decision = classify_us_location(job.location or "", company.get("type", ""))
                        if decision.status == "non_us":
                            non_us_relevant += 1
                        elif decision.status == "ambiguous":
                            ambiguous_relevant += 1

                _, disappeared = db.sync_company(name, jobs)

                # Eligibility has its own durable state. --seed establishes a
                # baseline without alerts; later ineligible->eligible changes
                # (for example a corrected US location) can alert exactly once.
                alert_candidates = []
                for job in jobs:
                    key = (job.company, job.external_id)
                    became_eligible = db.set_eligibility(
                        job,
                        key in eligible_keys,
                        reasons.get(key, "not eligible"),
                        baseline=seed,
                    )
                    if became_eligible:
                        alert_candidates.append(job)

                all_alert_candidates.extend(alert_candidates)

                if not seed:
                    for job in alert_candidates:
                        db.enqueue_alert(job)
                        db.enqueue_networking(job)

                synced_companies += 1
                scan_note = getattr(source, "last_scan_note", "")
                note = f", {scan_note}" if scan_note else ""
                location_note = ""
                if company.get("us_only", True):
                    location_note = f", {non_us_relevant} non-US relevant, {ambiguous_relevant} ambiguous"
                print(
                    f"{name}: {len(jobs)} fetched, {len(relevant_keys)} relevant, "
                    f"{len(eligible_keys)} US-eligible, {len(alert_candidates)} new US-eligible, "
                    f"{disappeared} disappeared{location_note}{note}"
                )
            except Exception as e:
                failed_companies += 1
                print(f"{name}: ERROR: {e}")

        print(f"New US-eligible alert candidates: {len(all_alert_candidates)}")
        if seed:
            print(
                f"Seed complete: {synced_companies} compan{'y' if synced_companies == 1 else 'ies'} "
                f"synced, {failed_companies} failed safely; eligibility baseline stored; no alerts or Tavily searches sent."
            )
            return

        key = os.getenv("TAVILY_API_KEY", "")
        budget = (
            TavilyBudget(key, local_daily_usage_getter=db.tavily_credits_used_last_24h)
            if enrich and key else None
        )

        # Networking is its own durable queue. Every queued job is revalidated
        # immediately before Tavily, so non-US/ambiguous/stale-cycle jobs cannot
        # consume credits even if they somehow exist in the queue.
        if enrich and budget is not None:
            networking_jobs = db.networking_due_jobs(int(os.getenv("MAX_NETWORKING_JOBS_PER_RUN", "30")))
            if networking_jobs:
                print(f"Networking jobs due: {len(networking_jobs)}")
            for job in networking_jobs:
                company = config_by_company.get(job.company)
                eligible, reason = _eligibility(job, company, threshold)
                if not eligible:
                    db.mark_networking_skipped(job, reason)
                    print(f"Networking skipped safely for {job.company} / {job.title}: {reason}")
                    continue

                try:
                    outcome = search_linkedin_public_index_outcome(job, db=db, budget=budget)
                    new_lead_count = 0
                    for lead in outcome.leads:
                        if db.add_lead_record(job, lead):
                            new_lead_count += 1

                    if outcome.completed:
                        db.mark_networking_complete(job)
                    else:
                        db.mark_networking_deferred(job, outcome.blocked_reason)

                    # If the internship alert was already delivered on an earlier
                    # run and deferred networking now found leads, send a follow-up.
                    all_leads = db.get_leads(job)
                    notified = db.networking_notified_count(job)
                    if db.alert_is_sent(job) and len(all_leads) > notified and new_lead_count > 0:
                        try:
                            discord_networking_followup(job, all_leads)
                            db.set_networking_notified_count(job, len(all_leads))
                        except Exception as e:
                            db.mark_networking_deferred(job, f"follow-up delivery failed: {e}")
                            print(f"Networking follow-up error for {job.company} / {job.title}: {e}")
                except Exception as e:
                    db.mark_networking_error(job, str(e))
                    print(f"Enrichment error for {job.company} / {job.title}: {e}")

        pending = db.pending_alert_jobs(int(os.getenv("MAX_ALERTS_PER_RUN", "20")))
        if pending:
            print(f"Pending alerts to deliver: {len(pending)}")

        for job in pending:
            company = config_by_company.get(job.company)
            eligible, reason = _eligibility(job, company, threshold)
            if not eligible:
                db.mark_alert_skipped(job, reason)
                print(f"Alert skipped safely for {job.company} / {job.title}: {reason}")
                continue

            leads = db.get_leads(job)
            networking_note = ""
            if enrich and budget is not None and not db.networking_is_complete(job):
                networking_note = budget.block_reason or "networking lookup is queued for a later run"
            elif enrich and not key:
                networking_note = "Tavily key not configured"

            try:
                discord_alert(job, leads, networking_note)
                db.mark_alert_sent(job)
                db.set_networking_notified_count(job, len(leads))
            except Exception as e:
                db.mark_alert_error(job, str(e))
                print(f"Alert delivery error for {job.company} / {job.title}: {e}")

        if budget is not None:
            remaining = budget.remaining_before_search
            remaining_text = "unknown" if remaining is None else str(max(0, remaining - budget.spent_this_run))
            print(
                f"Tavily searches this run: {budget.spent_this_run}; "
                f"local Tavily credits recorded last 24h: {db.tavily_credits_used_last_24h()}; "
                f"estimated key credits remaining: {remaining_text}"
            )
    finally:
        db.close()



def bootstrap_alerts(config_path: str, threshold: float):
    """Send currently active qualifying jobs to Discord once.

    Zero Tavily searches. Existing delivered alerts are not resent.
    """
    import time
    from src.models import Job

    load_dotenv(ROOT / ".env")
    db = JobDB(_db_path())

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        configs = {
            c["name"]: c
            for c in cfg.get("companies", [])
            if c.get("enabled", True)
        }

        rows = db.conn.execute(
            """
            SELECT company, external_id, title, location, url, source,
                   posted_at, description, score
            FROM jobs
            WHERE is_active=1
            ORDER BY score DESC, company, title
            """
        ).fetchall()

        eligible = []
        skipped_already_sent = 0
        skipped_ineligible = 0

        for row in rows:
            job = Job(*row)
            company = configs.get(job.company)

            ok, reason = _eligibility(job, company, threshold)

            if not ok:
                skipped_ineligible += 1
                continue

            if db.alert_is_sent(job):
                skipped_already_sent += 1
                continue

            eligible.append(job)

        print("BOOTSTRAP ALERTS")
        print("Tavily searches: 0")
        print(f"Qualifying unsent jobs: {len(eligible)}")
        print(f"Already alerted: {skipped_already_sent}")
        print(f"Active but ineligible: {skipped_ineligible}")

        if not eligible:
            print("Nothing new to bootstrap.")
            return

        print("\nSending:")
        for job in eligible:
            print(f"  {job.company} — {job.title} — {job.location}")

        sent = 0
        failed = 0

        for job in eligible:
            db.enqueue_alert(job)

            try:
                leads = db.get_leads(job)
                discord_alert(job, leads, "")
                db.mark_alert_sent(job)
                db.set_networking_notified_count(job, len(leads))
                sent += 1
                print(f"SENT  {job.company} — {job.title}")
            except Exception as e:
                failed += 1
                db.mark_alert_error(job, str(e))
                print(f"ERROR {job.company} — {job.title}: {e}")

            time.sleep(0.5)

        print(f"\nBootstrap complete: {sent} sent, {failed} failed.")
        print("Tavily searches used: 0")

    finally:
        db.close()


def bootstrap_networking(config_path: str, threshold: float):
    """One-time recruiter enrichment for current delivered internship alerts.

    Uses only the reusable company-level recruiter search.
    Never performs exact-post or UF searches.
    """
    import requests
    from src.models import Job

    load_dotenv(ROOT / ".env")
    db = JobDB(_db_path())

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        configs = {
            c["name"]: c
            for c in cfg.get("companies", [])
            if c.get("enabled", True)
        }

        key = os.getenv("TAVILY_API_KEY", "")
        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

        if not key:
            raise SystemExit("TAVILY_API_KEY is missing")
        if not webhook:
            raise SystemExit("DISCORD_WEBHOOK_URL is missing")

        rows = db.conn.execute(
            """
            SELECT j.company, j.external_id, j.title, j.location, j.url,
                   j.source, j.posted_at, j.description, j.score
            FROM jobs j
            JOIN alert_queue q
              ON q.company=j.company AND q.external_id=j.external_id
            WHERE j.is_active=1
              AND q.delivered_at IS NOT NULL
            ORDER BY j.company, j.score DESC
            """
        ).fetchall()

        grouped = {}

        for row in rows:
            job = Job(*row)
            company_cfg = configs.get(job.company)

            eligible, _ = _eligibility(job, company_cfg, threshold)
            if not eligible:
                continue

            grouped.setdefault(job.company, []).append(job)

        if not grouped:
            print("No currently alerted eligible jobs found.")
            return

        # Bootstrap may NEVER exceed four new Tavily searches in one run,
        # even if the normal configured per-run limit is higher.
        configured_cap = int(os.getenv("TAVILY_MAX_CREDITS_PER_RUN", "20"))
        bootstrap_cap = max(0, min(4, configured_cap))

        budget = TavilyBudget(
            key,
            max_credits_per_run=bootstrap_cap,
            local_daily_usage_getter=db.tavily_credits_used_last_24h,
        )

        # Process companies whose recruiter search is already cached first.
        ordered = []

        for company_name, jobs in grouped.items():
            representative = jobs[0]
            specs = selected_search_specs(
                representative,
                kinds=["recruiter"],
            )

            if not specs:
                continue

            spec = specs[0]
            cached = db.get_cached_search(
                spec.cache_key,
                spec.ttl_hours,
            ) is not None

            ordered.append(
                (0 if cached else 1, company_name, jobs)
            )

        ordered.sort(key=lambda x: (x[0], x[1]))

        print("BOOTSTRAP NETWORKING")
        print(f"Current qualifying companies: {len(ordered)}")
        print(f"Maximum NEW Tavily credits this run: {bootstrap_cap}")
        print("Search type: recruiter only")
        print()

        digests_sent = 0

        for cache_order, company_name, jobs in ordered:
            representative = jobs[0]

            print(
                f"{company_name}: "
                f"{len(jobs)} current alert(s), "
                f"{'cached recruiter search' if cache_order == 0 else 'recruiter search needed'}"
            )

            existing_recruiters = int(
                db.conn.execute(
                    """
                    SELECT COUNT(DISTINCT result_url)
                    FROM leads
                    WHERE company=? AND kind='recruiter'
                    """,
                    (company_name,),
                ).fetchone()[0]
            )

            if existing_recruiters:
                print(
                    f"  {existing_recruiters} recruiter lead(s) already stored; "
                    "skipping new Tavily search"
                )
                continue

            for job in jobs:
                db.enqueue_networking(job)

            outcome = search_linkedin_public_index_outcome(
                representative,
                db=db,
                budget=budget,
                kinds=["recruiter"],
            )

            if not outcome.completed:
                reason = outcome.blocked_reason or "Tavily search deferred"

                for job in jobs:
                    db.mark_networking_deferred(job, reason)

                print(f"  DEFERRED: {reason}")
                continue

            # Recruiter results are company-level, so attach the same
            # qualified contacts to all current qualifying jobs at that company.
            for job in jobs:
                for lead in outcome.leads:
                    db.add_lead_record(job, lead)

                db.mark_networking_complete(job)

            # Avoid sending the same company digest again on a rerun.
            should_notify = any(
                len(db.get_leads(job)) > db.networking_notified_count(job)
                for job in jobs
            )

            if not outcome.leads:
                print("  0 qualified recruiter leads")
                continue

            print(
                f"  {len(outcome.leads)} qualified recruiter lead(s), "
                f"{outcome.searches_used} new credit(s)"
            )

            if not should_notify:
                print("  Discord already notified")
                continue

            lines = [
                f"🔗 **{company_name} Networking Leads**",
                f"Applies to {len(jobs)} current qualifying internship alert(s).",
                "",
            ]

            for lead in outcome.leads[:8]:
                name = lead.title or "LinkedIn profile"
                lines.append(f"• **{name}** — {lead.url}")

            lines.extend([
                "",
                "_These are company-level university recruiting / talent contacts, "
                "not necessarily the hiring manager for a specific requisition._",
            ])

            message = "\n".join(lines)

            r = requests.post(
                webhook,
                json={"content": message},
                timeout=15,
            )
            r.raise_for_status()

            for job in jobs:
                db.set_networking_notified_count(
                    job,
                    len(db.get_leads(job)),
                )

            digests_sent += 1
            print("  Discord networking digest sent")

        remaining = budget.remaining_before_search
        remaining_text = (
            "unknown"
            if remaining is None
            else str(max(0, remaining - budget.spent_this_run))
        )

        print()
        print("BOOTSTRAP NETWORKING COMPLETE")
        print(f"New Tavily credits used: {budget.spent_this_run}")
        print(f"Discord company digests sent: {digests_sent}")
        print(
            f"Local Tavily credits recorded last 24h: "
            f"{db.tavily_credits_used_last_24h()}"
        )
        print(f"Estimated Tavily credits remaining: {remaining_text}")

        if budget.block_reason:
            print(f"Budget status: {budget.block_reason}")

    finally:
        db.close()

def deep_enrich(config_path: str, company_name: str, external_id: str, include_uf: bool = False):
    """Explicit paid deep-search for one already stored US-eligible posting.

    Automatic runs already use exact-post, post-author, and reusable recruiter
    searches. This command reruns that path for a selected job; UF alumni lookup
    is an additional opt-in because it is broader and historically lower-yield.
    """
    load_dotenv(ROOT / ".env")
    db = JobDB(_db_path())
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        configs = {c["name"]: c for c in cfg.get("companies", []) if c.get("enabled", True)}
        company = configs.get(company_name)
        if not company:
            raise SystemExit(f"Unknown enabled company: {company_name}")

        row = db.conn.execute(
            """
            SELECT company, external_id, title, location, url, source, posted_at, description, score
            FROM jobs WHERE company=? AND external_id=? AND is_active=1
            """,
            (company_name, external_id),
        ).fetchone()
        if not row:
            raise SystemExit(f"Active job not found: {company_name} / {external_id}")
        from src.models import Job
        job = Job(*row)
        eligible, reason = _eligibility(job, company, 12.0)
        if not eligible:
            raise SystemExit(f"Deep enrich blocked safely: {reason}")

        key = os.getenv("TAVILY_API_KEY", "")
        if not key:
            raise SystemExit("TAVILY_API_KEY is missing")
        kinds = ["exact_post", "recruiter"]
        if include_uf:
            kinds.append("uf_engineer")
        budget = TavilyBudget(key, local_daily_usage_getter=db.tavily_credits_used_last_24h)
        outcome = search_linkedin_public_index_outcome(job, db=db, budget=budget, kinds=kinds)
        for lead in outcome.leads:
            db.add_lead_record(job, lead)

        print(f"Deep enrich: {job.company} — {job.title}")
        print(f"Search kinds: {', '.join(kinds)}")
        print(f"New Tavily credits used: {outcome.searches_used}")
        print(f"Completed: {outcome.completed}")
        if outcome.blocked_reason:
            print(f"Blocked reason: {outcome.blocked_reason}")
        print(f"Qualified leads: {len(outcome.leads)}")
        for lead in outcome.leads:
            print(f"  [{lead.kind}] {lead.title} — {lead.url}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "companies.yaml"))
    parser.add_argument("--threshold", type=float, default=12.0)
    parser.add_argument("--no-enrich", action="store_true")
    parser.add_argument("--seed", action="store_true", help="Store current matches without alerting")
    parser.add_argument("--preflight", action="store_true", help="Validate setup without Tavily Search or Discord")
    parser.add_argument("--deep-enrich", nargs=2, metavar=("COMPANY", "REQ"), help="Rerun exact-post, author, and recruiter enrichment for one stored job")
    parser.add_argument("--include-uf", action="store_true", help="Also spend/cache the UF-engineer search during --deep-enrich")
    parser.add_argument("--roster-check", action="store_true", help="Fetch enabled sources only; zero DB/Tavily/Discord side effects")
    parser.add_argument("--bootstrap-alerts", action="store_true", help="Send currently active qualifying jobs to Discord once; zero Tavily")
    parser.add_argument("--bootstrap-networking", action="store_true", help="One-time recruiter enrichment for current Discord jobs")
    parser.add_argument("--company", help="Limit --roster-check to one company name")
    args = parser.parse_args()
    if args.preflight:
        preflight(args.config)
    elif args.roster_check:
        roster_check(args.config, args.threshold, company_name=args.company)
    elif args.deep_enrich:
        deep_enrich(args.config, args.deep_enrich[0], args.deep_enrich[1], include_uf=args.include_uf)
    elif args.bootstrap_alerts:
        bootstrap_alerts(args.config, args.threshold)
    elif args.bootstrap_networking:
        bootstrap_networking(args.config, args.threshold)
    else:
        run(args.config, args.threshold, not args.no_enrich, seed=args.seed)
