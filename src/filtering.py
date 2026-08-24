import os
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.models import Job


INTERNSHIP_TERMS = [
    "intern", "internship", "co-op", "coop", "student",
]

CE_TERMS = {
    "computer engineering": 6,
    "electrical engineering": 4,
    "embedded": 7,
    "firmware": 7,
    "fpga": 8,
    "asic": 8,
    "rtl": 8,
    "verilog": 8,
    "systemverilog": 8,
    "vhdl": 8,
    "soc": 6,
    "silicon": 6,
    "hardware": 5,
    "computer architecture": 8,
    "digital design": 7,
    "verification": 6,
    "validation": 4,
    "semiconductor": 6,
    "chip": 5,
    "cuda": 5,
    "gpu": 5,
    "compiler": 4,
    "systems software": 4,
    "systems engineer": 3,
    "software engineer": 3,
    "software engineering": 3,
    "c++": 3,
    "c/c++": 4,
    "linux": 2,
    "kernel": 5,
    "driver": 5,
    "device driver": 6,
    "robotics": 3,
    "machine learning": 2,
}

NEGATIVE_TERMS = {
    "senior": -12,
    "staff": -14,
    "principal": -16,
    "manager": -16,
    "director": -18,
    "phd required": -16,
    "5+ years": -12,
    "7+ years": -14,
    "10+ years": -16,
}

NON_TECH_TITLE_TERMS = [
    "marketing", "sales", "finance", "accounting", "legal", "human resources",
    "hr intern", "recruiting", "communications intern", "business intern",
]

SECTOR_KEYWORDS = {
    "hardware": {
        "asic": 18, "rtl": 18, "verilog": 17, "systemverilog": 18, "fpga": 16,
        "vlsi": 16, "digital design": 18, "digital circuit design": 18,
        "circuit design": 14, "verification": 12, "validation": 12,
        "hardware": 10, "computer architecture": 20, "microarchitecture": 17,
        "cpu": 10, "gpu": 8, "memory hierarchy": 12, "cache": 9, "soc": 12,
        "silicon": 11, "semiconductor": 12, "dft": 14, "physical design": 15,
        "embedded": 12, "firmware": 11, "rtos": 10, "device driver": 12,
        "systems software": 8, "c++": 6, "low level": 8,
    },
    "swe": {
        "software engineer": 18, "software engineering": 18, "systems software": 20,
        "backend": 12, "frontend": 12, "full stack": 11, "infrastructure": 10,
        "platform": 8, "distributed systems": 12, "cloud": 8, "api": 10,
        "rest": 8, "database": 8, "sql": 8, "linux": 7, "kernel": 9,
        "networking": 7, "python": 7, "javascript": 7, "typescript": 7,
        "docker": 7, "nginx": 7, "authentication": 6, "realtime": 8,
        "algorithms": 8, "data structures": 8, "c++": 7,
    },
    "data_ml": {
        "data science": 18, "machine learning": 18, "ml engineer": 18,
        "deep learning": 18, "artificial intelligence": 17, "ai": 14,
        "applied scientist": 16, "python": 9, "sql": 8,
        "pandas": 10, "numpy": 10, "scikit": 9, "xgboost": 12,
        "pytorch": 12, "tensorflow": 12, "recommendation": 10,
        "computer vision": 12, "nlp": 12, "optimization": 8,
        "statistical modeling": 10, "forecasting": 8, "anomaly detection": 10,
    },
}


@dataclass
class EligibilityResult:
    status: str = "uncertain"
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
    degree_match: bool | None = None
    job_type_match: bool | None = None
    term_match: bool | None = None
    graduation_window_match: bool | None = None
    special_program_match: bool | None = None


def clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _text(job: Job) -> tuple[str, str]:
    title = (job.title or "").lower()
    text = " ".join([job.title or "", job.description or "", job.location or ""]).lower()
    return title, clean_html(text)


def _contains_term(text: str, term: str) -> bool:
    """Match a keyword as a token and allow a simple plural where useful.

    Plain substring checks make ``ai`` match ``paid`` and ``intern`` match
    ``internal``. Non-word boundaries also work for technical tokens such as
    C++ and C/C++.
    """
    value = str(term or "").strip().lower()
    if not value:
        return False
    plural = "s?" if value.isalpha() else ""
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(value)}{plural}(?![a-z0-9])", text.lower()))


def _fit_points(job: Job) -> float:
    title, text = _text(job)
    fit = 0.0
    for term, weight in CE_TERMS.items():
        if _contains_term(text, term):
            fit += weight * (1.5 if _contains_term(title, term) else 1.0)
    return fit


def _title_years(title: str) -> set[int]:
    years = {int(y) for y in re.findall(r"\b(20\d{2})\b", title or "")}
    years.update(2000 + int(y) for y in re.findall(r"\bFY\s*([0-9]{2})\b", title or "", re.I))
    return years


def matches_target_year(job: Job, target_year: int | None) -> bool:
    """Reject only when the TITLE explicitly names a different internship year."""
    if not target_year:
        return True
    years = _title_years(job.title or "")
    return not years or int(target_year) in years


def _candidate_eligible_special_programs() -> bool:
    value = os.getenv("CANDIDATE_SPECIAL_PROGRAM_ELIGIBLE", "false").strip().lower()
    return value in {"1", "true", "yes", "y"}


def evaluate_eligibility(job: Job, candidate_graduation_year: int | None = None) -> EligibilityResult:
    """Return a structured eligibility result before fit scoring is applied."""
    title, text = _text(job)
    status = "eligible"
    reasons: list[str] = []
    candidate_grad = candidate_graduation_year or int(os.getenv("EXPECTED_GRAD_YEAR", "2029"))

    # Internship / job-type gate.
    if not any(_contains_term(text, term) for term in INTERNSHIP_TERMS):
        status = "ineligible"
        reasons.append("no internship or student term detected")
        return EligibilityResult(status=status, reasons=reasons, confidence=0.98,
                                degree_match=False, job_type_match=False, term_match=False)

    non_intern_job_type_patterns = [
        r"\bfull[- ]time\b",
        r"\bnew grad\b",
        r"\bnew-grad\b",
        r"\bprogram manager\b",
        r"\bproduct manager\b",
        r"\bmanager\b",
        r"\bstaff\b",
        r"\bprincipal\b",
        r"\bdirector\b",
        r"\bsenior\b",
    ]
    if re.search(r"\b(?:program manager|product manager|manager|staff|principal|director|senior)\b", title, re.I):
        if not re.search(r"\bintern\b|\bco[- ]?op\b|\bstudent\b", title, re.I):
            status = "ineligible"
            reasons.append("non-intern role designation")
    for pattern in non_intern_job_type_patterns:
        if re.search(pattern, text, re.I) and not re.search(r"\bintern\b|\bco[- ]?op\b|\bstudent\b", text, re.I):
            status = "ineligible"
            reasons.append("non-intern role detected")
            break

    allows_undergrad = bool(re.search(r"\b(?:undergrad|bachelor'?s?|undergraduate|bs\b|b\.s\.)\b", text, re.I))
    graduate_only_title_patterns = [
        r"\bph\.?d\.?\b",
        r"\bdoctoral\b",
        r"\bmaster(?:'s|s)\b",
        r"\bm\.?s\.?\b",
        r"\bmba\b",
        r"\bgraduate[- ](?:degree|student|research)",
    ]
    if any(re.search(pattern, title, re.I) is not None for pattern in graduate_only_title_patterns):
        if not allows_undergrad:
            status = "ineligible"
            reasons.append("graduate-degree-only internship title")

    graduate_only_patterns = [
        r"\bph\.?d\.?\s+(?:degree\s+)?required\b",
        r"\bdoctoral\s+degree\s+required\b",
        r"\bmaster'?s?\s+degree\s+required\b",
        r"\bmasters?\s+degree\s+required\b",
        r"\b(?:m\.s\.|ms)\s+students?\s+only\b",
        r"\bgraduate\s+students?\s+only\b",
        r"\bph\.?d\.?\s+students?\s+only\b",
        r"\bmaster'?s?\s+students?\s+only\b",
        r"\bmasters?\s+students?\s+only\b",
    ]
    if any(re.search(pattern, text, re.I) is not None for pattern in graduate_only_patterns):
        if not allows_undergrad:
            status = "ineligible"
            reasons.append("graduate-degree-only requirement")

    # Respect explicit graduate-window exclusions.
    year_pattern = re.compile(r"\b(?:expected\s+)?graduation(?:\s+(?:date|year))?\b.{0,30}\b(20\d{2})\b|\b(?:graduate|graduating)\s+(?:by|before|on or before|no later than)\b.{0,20}\b(20\d{2})\b|\b(20\d{2})\b.{0,25}\b(?:graduate|graduating|graduation)\b", re.I)
    for match in year_pattern.finditer(text):
        year_candidates = [int(g) for g in match.groups() if g and g.isdigit()]
        for year in year_candidates:
            context = text[max(0, match.start() - 40):match.end() + 40]
            if re.search(r"\b(?:or later|or after|and later|or beyond)\b", context, re.I):
                continue
            if year < candidate_grad:
                status = "ineligible"
                reasons.append(f"explicit graduation window excludes expected grad year {candidate_grad}: {year}")
                break
        if status == "ineligible":
            break

    skillbridge_patterns = [
        r"\bskillbridge\b",
        r"\bdod\s+skillbridge\b",
        r"\bmilitary\s+transition\b",
        r"\breturnship\b",
    ]
    if any(re.search(p, text, re.I) is not None for p in skillbridge_patterns):
        if not _candidate_eligible_special_programs():
            status = "ineligible"
            reasons.append("special program excluded by candidate configuration")

    if status == "eligible" and not re.search(
        r"\b(?:undergrad|bachelor'?s?|undergraduate|bs\b|b\.s\.|master(?:'s|s)|ms\b|m\.s\.|ph\.?d\.?|doctoral)\b",
        text,
        re.I,
    ):
        status = "uncertain"
        reasons.append("degree requirement not explicit")

    if status == "eligible" and re.search(r"\b(?:undergrad|undergraduate|bachelor'?s?|bs\b|b\.s\.)\b", text, re.I):
        confidence = 0.87
    elif status == "uncertain":
        confidence = 0.55
    else:
        confidence = 0.97 if status == "ineligible" else 0.74

    return EligibilityResult(
        status=status,
        reasons=reasons,
        confidence=confidence,
        degree_match=allows_undergrad or not any(re.search(p, text, re.I) is not None for p in [
            r"\bph\.?d\.?\b.*\bintern",
            r"\bmaster(?:'s|s)\b.*\bintern",
            r"\bmaster'?s?\s+degree\s+required\b",
            r"\bph\.?d\.?\s+students?\s+only\b",
            r"\bgraduate\s+students?\s+only\b",
        ]),
        job_type_match=bool(re.search(r"\bintern\b|\bco[- ]?op\b|\bstudent\b", text, re.I)),
        term_match=bool(re.search(r"\bintern\b|\bco[- ]?op\b|\bstudent\b", title, re.I)),
        graduation_window_match=not bool(re.search(r"\b(?:graduate|graduating|graduation)\b.{0,40}\b(?:before|by|no later than)\b", text, re.I)),
        special_program_match=not any(re.search(p, text, re.I) is not None for p in [r"\bskillbridge\b", r"\breturnship\b"]),
    )


def academic_ineligibility_reason(job: Job) -> str | None:
    """Backward-compatible helper used by older tests and callers."""
    result = evaluate_eligibility(job)
    if result.status == "ineligible":
        return "; ".join(result.reasons) if result.reasons else "ineligible"
    return None


def score_sector_fit(job: Job) -> dict[str, float]:
    """Score each target sector without requiring a prior fit decision."""
    _, text = _text(job)
    sector_scores = {k: 0.0 for k in ("hardware", "swe", "data_ml")}
    for sector, terms in SECTOR_KEYWORDS.items():
        score = 0.0
        for term, weight in terms.items():
            if _contains_term(text, term):
                score += weight
        if sector == "hardware":
            if _contains_term(text, "hardware") or _contains_term(text, "computer architecture"):
                score += 8
        sector_scores[sector] = round(score, 2)
    return sector_scores


def relevance_score(job: Job, target_year: int | None = None) -> float:
    title, text = _text(job)

    if not any(_contains_term(text, term) for term in INTERNSHIP_TERMS):
        return -20.0
    if not matches_target_year(job, target_year):
        return -30.0

    eligibility = evaluate_eligibility(job)
    if eligibility.status == "ineligible":
        return -50.0

    sector_scores = score_sector_fit(job)
    best_sector_score = max(sector_scores.values())
    score = 10.0 + best_sector_score
    if eligibility.status == "uncertain":
        score -= 3.0

    for term, weight in NEGATIVE_TERMS.items():
        if term in text:
            score += weight

    if any(_contains_term(title, term) for term in NON_TECH_TITLE_TERMS):
        score -= 20

    if re.search(r"\bUS\b|United States|, [A-Z]{2}\b", job.location or ""):
        score += 1

    return round(score, 2)


def is_relevant(job: Job, threshold: float = 12.0, target_year: int | None = None) -> bool:
    _, text = _text(job)

    if not any(_contains_term(text, term) for term in INTERNSHIP_TERMS):
        job.score = -20.0
        return False

    if not matches_target_year(job, target_year):
        job.score = -30.0
        return False

    eligibility = evaluate_eligibility(job)
    if eligibility.status == "ineligible":
        job.score = -50.0
        return False

    fit = score_sector_fit(job)
    if all(score < 4 for score in fit.values()):
        job.score = 10.0
        return False

    job.score = relevance_score(job, target_year=target_year)
    return job.score >= threshold
