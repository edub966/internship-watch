import re
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


def clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _text(job: Job) -> tuple[str, str]:
    title = (job.title or "").lower()
    text = " ".join([job.title or "", job.description or "", job.location or ""]).lower()
    return title, clean_html(text)


def _fit_points(job: Job) -> float:
    title, text = _text(job)
    fit = 0.0
    for term, weight in CE_TERMS.items():
        if term in text:
            fit += weight * (1.5 if term in title else 1.0)
    return fit


def _title_years(title: str) -> set[int]:
    years = {int(y) for y in re.findall(r"\b(20\d{2})\b", title or "")}
    years.update(2000 + int(y) for y in re.findall(r"\bFY\s*([0-9]{2})\b", title or "", re.I))
    return years


def matches_target_year(job: Job, target_year: int | None) -> bool:
    """Reject only when the TITLE explicitly names a different internship year.

    Titles without a year remain eligible. We intentionally do not scan the body
    for years because copyright/legal text can mention unrelated years.
    """
    if not target_year:
        return True
    years = _title_years(job.title or "")
    return not years or int(target_year) in years



def academic_ineligibility_reason(job: Job) -> str | None:
    """Return a reason only for academic requirements that clearly exclude us.

    Conservative on purpose: uncertain wording is allowed through.
    """
    _, text = _text(job)

    graduate_only_patterns = [
        r"\bph\.?d\.?\s+(?:degree\s+)?required\b",
        r"\bdoctoral\s+degree\s+required\b",
        r"\bmaster'?s?\s+degree\s+required\b",
        r"\bmasters?\s+degree\s+required\b",
        r"\bgraduate\s+students?\s+only\b",
        r"\bph\.?d\.?\s+students?\s+only\b",
        r"\bmaster'?s?\s+students?\s+only\b",
        r"\bminimum\b.{0,40}\b(?:master'?s?|ph\.?d\.?|doctoral)\b",
    ]

    for pattern in graduate_only_patterns:
        if re.search(pattern, text, re.I):
            # Do not reject if bachelor's/undergraduate is explicitly accepted too.
            context_has_undergrad = re.search(
                r"\b(?:bachelor'?s?|undergraduate)\b", text, re.I
            )
            if not context_has_undergrad:
                return "graduate-degree-only requirement"

    # Explicit requirements to finish school too early for our target.
    # We intentionally only hard-reject 2027-or-earlier language rather than
    # trying to infer eligibility from every random year in the description.
    grad_patterns = [
        r"\bgraduat(?:e|es|ing|ion)\b.{0,50}\b(?:in|by|before|on or before|no later than)\b.{0,20}\b(20\d{2})\b",
        r"\b(?:expected\s+)?graduation\s+(?:date|year)?\b.{0,40}\b(20\d{2})\b",
        r"\b(20\d{2})\b.{0,40}\bgraduat(?:e|es|ing|ion)\b",
    ]

    for pattern in grad_patterns:
        for match in re.finditer(pattern, text, re.I):
            year = int(match.group(1))

            # "2027 or later" does NOT exclude us.
            context = text[max(0, match.start() - 30):match.end() + 30]
            if re.search(r"\b(?:or later|or after|and later|or beyond)\b", context, re.I):
                continue

            if year <= 2027:
                return f"graduation requirement too early ({year})"

    return None

def relevance_score(job: Job, target_year: int | None = None) -> float:
    title, text = _text(job)

    if not any(term in text for term in INTERNSHIP_TERMS):
        return -20.0
    if not matches_target_year(job, target_year):
        return -30.0
    if academic_ineligibility_reason(job):
        return -40.0

    fit = _fit_points(job)
    score = 10.0 + fit

    for term, weight in NEGATIVE_TERMS.items():
        if term in text:
            score += weight

    if any(term in title for term in NON_TECH_TITLE_TERMS):
        score -= 20

    if re.search(r"\bUS\b|United States|, [A-Z]{2}\b", job.location or ""):
        score += 1

    return round(score, 2)


def is_relevant(job: Job, threshold: float = 12.0, target_year: int | None = None) -> bool:
    title, text = _text(job)

    if not any(term in text for term in INTERNSHIP_TERMS):
        job.score = -20.0
        return False

    if not matches_target_year(job, target_year):
        job.score = -30.0
        return False

    if academic_ineligibility_reason(job):
        job.score = -40.0
        return False

    fit = _fit_points(job)
    if fit < 2:
        job.score = round(10.0 + fit, 2)
        return False

    job.score = relevance_score(job, target_year=target_year)
    return job.score >= threshold
