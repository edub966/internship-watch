import os
from typing import List

import requests

from src.enrich import Lead
from src.filtering import evaluate_eligibility, score_sector_fit
from src.models import Job


_KIND_LABELS = {
    "exact_post": "Posting-specific LinkedIn posts",
    "recruiter": "Recruiting contacts",
    "uf_engineer": "UF / technical contacts",
    "other": "Other leads",
}

_SECTOR_LABELS = {
    "hardware": "Hardware / Architecture",
    "swe": "SWE / Systems",
    "data_ml": "Data / ML / AI",
}

_RESUME_LABELS = {
    "hardware": "Hardware",
    "swe": "SWE",
    "data_ml": "Data/ML",
}


def _lead_section(leads: List[Lead]) -> str:
    if not leads:
        return ""
    grouped = {}
    for lead in leads[:8]:
        grouped.setdefault(lead.kind, []).append(lead)

    sections = []
    for kind in ("exact_post", "recruiter", "uf_engineer", "other"):
        rows = grouped.get(kind, [])
        if not rows:
            continue
        lines = []
        for lead in rows[:3]:
            label = (lead.title or "LinkedIn result")[:90]
            lines.append(f"• {label}\n{lead.url}")
        sections.append(f"**{_KIND_LABELS[kind]}**\n" + "\n".join(lines))
    return "\n\n".join(sections)


def format_match_details(job: Job) -> str:
    """Format candidate eligibility and sector fit for alert consumers."""
    eligibility = evaluate_eligibility(job)
    if eligibility.status == "eligible":
        eligibility_line = "Eligibility: ✅ Eligible"
    elif eligibility.status == "uncertain":
        eligibility_line = "Eligibility: ⚠️ Verify"
    else:
        eligibility_line = "Eligibility: ❌ Ineligible"

    reason_line = ""
    if eligibility.reasons:
        reason_line = "\nReason: " + "; ".join(eligibility.reasons)[:300]

    scores = score_sector_fit(job)
    primary_sector = max(scores, key=scores.get)
    score_lines = "\n".join(
        f"{_SECTOR_LABELS[sector]}: {score:g}"
        for sector, score in scores.items()
    )
    return (
        f"{eligibility_line}{reason_line}\n"
        f"Primary track: {_SECTOR_LABELS[primary_sector]}\n"
        f"Recommended resume: {_RESUME_LABELS[primary_sector]}\n"
        f"Overall fit: {job.score:g}\n"
        f"Fit by track\n{score_lines}"
    )


def discord_alert(job: Job, leads: List[Lead], enrichment_note: str = "") -> None:
    match_details = format_match_details(job)
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        print(
            f"\nNEW: {job.company} | {job.title} | {job.location}\n"
            f"{match_details}\n{job.url}"
        )
        for lead in leads[:5]:
            print(f"  - [{lead.kind} {lead.score:.0f}] {lead.title}\n    {lead.url}")
        if enrichment_note:
            print(f"Networking note: {enrichment_note}")
        return

    desc = (
        f"**{job.company} — {job.title}**\n"
        f"Req: {job.external_id}\n"
        f"Location: {job.location or 'Not listed'}\n"
        f"{match_details}\n"
        f"Posted: {job.posted_at or 'Not listed'}\n"
        f"Apply: {job.url}"
    )
    lead_text = _lead_section(leads)
    if lead_text:
        desc += "\n\n" + lead_text
    if enrichment_note:
        desc += "\n\n_Networking lookup: " + enrichment_note[:300] + "_"

    payload = {
        "content": "🚨 New internship match",
        "embeds": [{"description": desc[:4000]}],
    }
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()


def discord_networking_followup(job: Job, leads: List[Lead]) -> None:
    """Send a separate networking update when leads arrive after the job alert."""
    if not leads:
        return
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        print(f"\nNETWORKING UPDATE: {job.company} | {job.title}")
        for lead in leads[:5]:
            print(f"  - [{lead.kind} {lead.score:.0f}] {lead.title}\n    {lead.url}")
        return

    desc = f"**{job.company} — {job.title}**\nReq: {job.external_id}\n\n" + _lead_section(leads)
    payload = {
        "content": "🔎 New outreach leads",
        "embeds": [{"description": desc[:4000]}],
    }
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()
