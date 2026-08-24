import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional
from urllib.parse import urlparse, urlunparse

import requests

from src.models import Job


@dataclass
class Lead:
    url: str
    title: str
    snippet: str
    query: str
    kind: str = "other"
    score: float = 0.0
    role_bucket: str = ""
    author_name: str = ""
    author_profile_url: str = ""
    connection_type: str = "potential_connection"
    affiliations: tuple[str, ...] = ()
    source_post_url: str = ""
    relevance_reason: str = ""


@dataclass(frozen=True)
class SearchSpec:
    kind: str
    query: str
    include_domains: tuple[str, ...]
    ttl_hours: int
    role_bucket: str = "general_engineering"
    max_results: int = 5
    search_depth: str = "basic"
    country: str = "united states"

    @property
    def cache_key(self) -> str:
        material = json.dumps(
            {
                "v": 3,
                "kind": self.kind,
                "role_bucket": self.role_bucket,
                "query": self.query,
                "domains": self.include_domains,
                "max_results": self.max_results,
                "search_depth": self.search_depth,
                "country": self.country,
            },
            sort_keys=True,
        )
        return "tavily:" + hashlib.sha256(material.encode()).hexdigest()


@dataclass
class EnrichmentOutcome:
    leads: List[Lead]
    completed: bool
    searches_used: int
    blocked_reason: str = ""


class TavilyBudget:
    """Hard guard around paid Tavily Search calls.

    The usage endpoint is checked before any search unless explicitly disabled.
    Search calls are basic-depth only, so each successful request costs 1 credit.
    """

    def __init__(
        self,
        api_key: str,
        max_credits_per_run: Optional[int] = None,
        reserve_credits: Optional[int] = None,
        require_usage_check: Optional[bool] = None,
        usage_getter=None,
        daily_credit_cap: Optional[int] = None,
        local_daily_usage_getter=None,
    ):
        self.api_key = api_key
        self.max_credits_per_run = max_credits_per_run if max_credits_per_run is not None else int(
            os.getenv("TAVILY_MAX_CREDITS_PER_RUN", "20")
        )
        self.reserve_credits = reserve_credits if reserve_credits is not None else int(
            os.getenv("TAVILY_CREDIT_RESERVE", "100")
        )
        if require_usage_check is None:
            require_usage_check = os.getenv("TAVILY_REQUIRE_USAGE_CHECK", "true").lower() not in {
                "0", "false", "no"
            }
        self.require_usage_check = require_usage_check
        self.usage_getter = usage_getter or self._fetch_usage
        self.daily_credit_cap = daily_credit_cap if daily_credit_cap is not None else int(
            os.getenv("TAVILY_DAILY_CREDIT_CAP", "60")
        )
        self.local_daily_usage_getter = local_daily_usage_getter
        self.local_spent_before_run = 0
        self.spent_this_run = 0
        self.remaining_before_search: Optional[int] = None
        self.usage_source = ""
        self.block_reason = ""
        self._checked = False

    def _fetch_usage(self) -> dict:
        r = requests.get(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=12,
        )
        r.raise_for_status()
        return r.json()

    def check(self) -> bool:
        if self._checked:
            return not self.block_reason
        self._checked = True

        if self.max_credits_per_run <= 0:
            self.block_reason = "per-run Tavily budget is 0"
            return False

        if self.daily_credit_cap <= 0:
            self.block_reason = "daily Tavily budget is 0"
            return False
        if self.local_daily_usage_getter is not None:
            try:
                self.local_spent_before_run = max(0, int(self.local_daily_usage_getter()))
            except Exception as e:
                self.block_reason = f"local Tavily usage ledger failed safely: {e}"
                return False
            if self.local_spent_before_run >= self.daily_credit_cap:
                self.block_reason = (
                    f"local rolling-24h Tavily cap reached ({self.daily_credit_cap}); "
                    f"{self.local_spent_before_run} recorded in last 24h"
                )
                return False

        try:
            payload = self.usage_getter()
            if not isinstance(payload, dict):
                raise ValueError("usage endpoint returned a non-object payload")

            def _whole_number(value):
                # Tavily may return null for a key-level limit when the API key
                # itself is not capped even though the account plan is capped.
                # Do not coerce null to zero; fall back to the account window.
                if value is None or isinstance(value, bool):
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            key_usage = payload.get("key") or {}
            account = payload.get("account") or {}

            key_used = _whole_number(key_usage.get("usage"))
            key_limit = _whole_number(key_usage.get("limit"))
            plan_used = _whole_number(account.get("plan_usage"))
            plan_limit = _whole_number(account.get("plan_limit"))

            # Prefer a concrete per-key cap when Tavily supplies one. If the key
            # limit is null/unlimited, guard against the account plan instead.
            # The account window is safer than combining a key's usage with an
            # account-wide limit, especially when several API keys exist.
            if key_used is not None and key_limit is not None and key_limit > 0:
                usage, limit = key_used, key_limit
                self.usage_source = "API key"
            elif plan_used is not None and plan_limit is not None and plan_limit > 0:
                usage, limit = plan_used, plan_limit
                self.usage_source = "account plan"
            else:
                raise ValueError(
                    "usage endpoint did not provide a usable key limit or account plan limit"
                )

            usage = max(0, usage)
            self.remaining_before_search = max(0, limit - usage)
        except Exception as e:
            if self.require_usage_check:
                self.block_reason = f"usage check failed safely: {e}"
                return False
            self.remaining_before_search = None

        if self.remaining_before_search is not None and self.remaining_before_search <= self.reserve_credits:
            self.block_reason = (
                f"Tavily reserve reached: {self.remaining_before_search} credits remain, "
                f"reserve is {self.reserve_credits}"
            )
            return False
        return True

    def allow_search(self) -> bool:
        if not self.check():
            return False
        if self.spent_this_run >= self.max_credits_per_run:
            self.block_reason = f"per-run Tavily cap reached ({self.max_credits_per_run})"
            return False
        if self.local_spent_before_run + self.spent_this_run >= self.daily_credit_cap:
            self.block_reason = (
                f"local rolling-24h Tavily cap reached ({self.daily_credit_cap})"
            )
            return False
        if self.remaining_before_search is not None:
            projected_remaining = self.remaining_before_search - self.spent_this_run - 1
            if projected_remaining < self.reserve_credits:
                self.block_reason = (
                    f"next search would cross Tavily reserve ({self.reserve_credits})"
                )
                return False
        return True

    def record_search(self) -> None:
        self.spent_this_run += 1


def _normalize_text(value: str) -> str:
    return " ".join((value or "").lower().replace("/", " ").replace("-", " ").replace("_", " ").split())


_RECRUITER_TERMS = (
    "recruiter", "recruiting", "talent acquisition", "talent partner",
    "early careers", "early talent", "university recruiting",
    "university recruiter", "university relations", "university programs",
    "university talent", "campus recruiter", "campus recruiting",
    "student programs", "sourcer", "staffing",
)

_TECHNICAL_CONNECTION_TERMS = (
    "engineer", "engineering", "architect", "developer", "scientist",
    "hardware", "firmware", "software", "silicon", "verification",
    "machine learning", "technical lead", "hiring manager",
)

_CONNECTION_PRIORITY = {
    "potential_connection": 0,
    "job_poster": 1,
    "technical_connection": 2,
    "recruiter": 3,
}


def _extract_affiliations(text: str) -> tuple[str, ...]:
    hay = f" {_normalize_text(text)} "
    affiliations = []
    if any(term in hay for term in (
        " university of florida ", " uf alum", " uf graduate", " florida gator", " gator alum",
    )):
        affiliations.append("UF")
    if any(term in hay for term in (
        " pi kappa alpha ", " pike fraternity ", " pike brother ", " pike alumn", " πκα ",
    )):
        affiliations.append("PIKE")
    return tuple(affiliations)


def _connection_type(text: str, *, exact_post: bool = False) -> str:
    hay = _normalize_text(text)
    if any(term in hay for term in _RECRUITER_TERMS):
        return "recruiter"
    if any(term in hay for term in _TECHNICAL_CONNECTION_TERMS):
        return "technical_connection"
    return "job_poster" if exact_post else "potential_connection"


def _looks_like_person_name(value: str, company: str = "") -> bool:
    candidate = " ".join(str(value or "").strip(" -|–—:'\"").split())
    tokens = candidate.split()
    if not 2 <= len(tokens) <= 5:
        return False
    rejected = {
        "linkedin", "post", "posts", "hiring", "intern", "internship", "jobs",
        "careers", "company", "team", "official", "recruiting",
    }
    company_tokens = set(_normalize_text(company).split())
    normalized = _normalize_text(candidate).split()
    if any(token in rejected for token in normalized):
        return False
    if company_tokens and set(normalized) <= company_tokens:
        return False
    return all(re.search(r"[A-Za-z]", token) and not token.isdigit() for token in tokens)


def _extract_post_author(title: str, url: str, company: str = "") -> str:
    clean_title = " ".join(str(title or "").split())
    patterns = (
        r"^(?P<name>.+?)(?:'s|’s)\s+(?:post|update)\b",
        r"^(?P<name>.+?)\s+on\s+linkedin\b",
        r"^(?P<name>.+?)\s*[|–—]\s*linkedin\b",
    )
    for pattern in patterns:
        match = re.search(pattern, clean_title, re.I)
        if match:
            candidate = match.group("name").strip()
            if _looks_like_person_name(candidate, company):
                return candidate

    try:
        path = urlparse(url).path
        slug = path.split("/posts/", 1)[1].split("/", 1)[0]
    except (IndexError, AttributeError):
        return ""
    slug = re.split(r"_activity[-_]", slug, maxsplit=1, flags=re.I)[0]
    slug = re.sub(r"[-_]\d+$", "", slug)
    candidate = " ".join(part.capitalize() for part in re.split(r"[-_]", slug) if part)
    return candidate if _looks_like_person_name(candidate, company) else ""


def _extract_profile_name(title: str) -> str:
    candidate = re.split(r"\s+[-|–—]\s+", str(title or ""), maxsplit=1)[0].strip()
    return candidate if _looks_like_person_name(candidate) else ""


def build_author_search_spec(job: Job, author_name: str) -> SearchSpec:
    company = " ".join((job.company or "").replace('"', "").split())
    author = " ".join((author_name or "").replace('"', "").split())
    return SearchSpec(
        kind="post_author",
        query=f"{author} {company} recruiter engineer hiring intern",
        include_domains=("linkedin.com/in",),
        ttl_hours=24 * 14,
        role_bucket=_role_bucket(job),
        max_results=10,
    )


def _token_bound_phrase(text: str, phrase: str) -> bool:
    normalized = _normalize_text(text)
    phrase_norm = _normalize_text(phrase)
    if not phrase_norm:
        return False
    if phrase_norm not in normalized:
        return False
    start = normalized.index(phrase_norm)
    end = start + len(phrase_norm)
    before_ok = start == 0 or not normalized[start - 1].isalnum()
    after_ok = end == len(normalized) or not normalized[end].isalnum()
    return before_ok and after_ok


def _company_matches_text(text: str, company: str) -> bool:
    """Require visible target-company evidence before treating a profile as relevant."""
    company_tokens = [
        token for token in re.findall(r"[a-z0-9]+", _normalize_text(company))
        if len(token) >= 2 and token not in {
            "the", "inc", "llc", "corp", "corporation", "company", "group",
        }
    ]
    if not company_tokens:
        return False
    hay_tokens = set(re.findall(r"[a-z0-9]+", _normalize_text(text)))
    return all(token in hay_tokens for token in company_tokens)


def _role_bucket(job: Job) -> str:
    title = _normalize_text(job.title or "")
    description = _normalize_text(job.description or "")
    combined = f"{title} {description}"

    hardware_phrases = (
        "asic", "rtl", "fpga", "vlsi", "physical design", "dft", "digital design",
        "silicon design", "chip design", "hardware", "computer architecture", "verification",
        "design verification", "semiconductor", "soc rtl", "system on chip",
    )
    firmware_phrases = (
        "firmware", "embedded", "rtos", "bsp", "microcontroller", "device firmware",
        "embedded linux", "driver development", "embedded driver",
    )
    software_phrases = (
        "software engineering", "systems software", "compiler", "cuda", "gpu software",
        "gpu", "kernel", "software infrastructure", "software engineer",
    )
    ml_phrases = (
        "machine learning", "deep learning", "artificial intelligence", "ai ml", "ml engineer",
        "ai software", "model training", "inference engine",
    )

    if any(_token_bound_phrase(combined, phrase) for phrase in ml_phrases):
        return "machine_learning"
    if any(_token_bound_phrase(combined, phrase) for phrase in hardware_phrases):
        return "hardware"
    if any(_token_bound_phrase(combined, phrase) for phrase in firmware_phrases):
        return "firmware_embedded"
    if any(_token_bound_phrase(combined, phrase) for phrase in software_phrases):
        return "software"
    return "general_engineering"


def _role_search_terms(bucket: str) -> str:
    mapping = {
        "hardware": "hardware ASIC RTL silicon design",
        "firmware_embedded": "firmware embedded systems",
        "software": "software engineering systems",
        "machine_learning": "AI ML machine learning",
        "general_engineering": "engineering hardware software",
    }
    return mapping.get(bucket, mapping["general_engineering"])


def _role_terms(job: Job) -> str:
    return _role_search_terms(_role_bucket(job))


def build_search_specs(job: Job) -> List[SearchSpec]:
    """Build domain-restricted Tavily searches without relying on Google operators."""
    clean_title = " ".join((job.title or "").replace('"', "").split())
    company = " ".join((job.company or "").replace('"', "").split())
    req = " ".join(str(job.external_id or "").replace('"', "").split())
    bucket = _role_bucket(job)
    role_terms = _role_search_terms(bucket)

    if company and clean_title.lower().startswith(company.lower() + " "):
        clean_title = clean_title[len(company):].strip()

    exact_parts = [company, clean_title]
    if req:
        exact_parts.append(req)
    exact_parts.extend(["internship", "hiring"])

    recruiter_query = f"{company} United States {role_terms} recruiter"
    return [
        SearchSpec(
            kind="exact_post",
            query=" ".join(exact_parts),
            include_domains=("linkedin.com/posts",),
            ttl_hours=18,
            role_bucket="exact_post",
            max_results=10,
        ),
        SearchSpec(
            kind="recruiter",
            query=recruiter_query,
            include_domains=("linkedin.com/in",),
            ttl_hours=24 * 14,
            role_bucket=bucket,
            max_results=10,
        ),
        SearchSpec(
            kind="uf_engineer",
            query=f"{company} University of Florida {role_terms} engineer",
            include_domains=("linkedin.com/in",),
            ttl_hours=24 * 14,
            role_bucket=bucket,
            max_results=5,
        ),
    ]


# Backwards-compatible helper used by earlier tests/local scripts.
def build_queries(job: Job) -> List[str]:
    return [spec.query for spec in build_search_specs(job)]


_ALLOWED_KINDS = {"exact_post", "recruiter", "uf_engineer"}


def selected_search_specs(job: Job, kinds: Optional[Iterable[str]] = None) -> List[SearchSpec]:
    """Return the Tavily searches permitted for this run.

    Production defaults to one requisition-specific post search plus the
    reusable company/role recruiter search. UF-engineer lookup remains opt-in.
    """
    if kinds is None:
        raw = os.getenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post,recruiter")
        kinds = [x.strip() for x in raw.split(",") if x.strip()]
    requested = []
    for kind in kinds:
        if kind in _ALLOWED_KINDS and kind not in requested:
            requested.append(kind)
    by_kind = {s.kind: s for s in build_search_specs(job)}
    return [by_kind[k] for k in requested if k in by_kind]


def _canonical_linkedin_url(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return ""
    host = (p.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    path = p.path.rstrip("/") or "/"
    if "/in/" in path:
        tail = path.split("/in/", 1)[1]
        parts = tail.split("/")
        if parts and parts[0]:
            path = "/in/" + parts[0]
    return urlunparse(("https", "www.linkedin.com", path, "", "", ""))


def _url_matches_kind(url: str, kind: str) -> bool:
    path = urlparse(url).path.lower()
    if kind == "exact_post":
        return path.startswith("/posts/") or "/posts/" in path
    if kind in {"recruiter", "uf_engineer", "post_author"}:
        return path.startswith("/in/") or "/in/" in path
    return True


def _score(job: Job, title: str, snippet: str, url: str, tavily_score: float = 0.0, kind: str = "other") -> float:
    hay = f"{title} {snippet}".lower()
    s = 0.0

    for token in job.company.lower().split():
        if token in hay:
            s += 2

    title_tokens = [t for t in _normalize_text(job.title).split() if len(t) >= 4]
    s += min(sum(1 for t in title_tokens if t in _normalize_text(hay).split()), 6)

    if job.external_id and str(job.external_id).lower() in hay:
        s += 8

    if "university of florida" in hay or " uf " in f" {hay} ":
        s += 4

    for term in [
        "recruiter", "recruiting", "early careers", "talent acquisition", "talent partner",
        "university recruiting", "university recruiter", "university relations",
        "campus recruiter", "campus recruiting", "intern", "engineer", "manager", "hardware",
        "firmware", "silicon", "verification", "software",
    ]:
        if term in hay:
            s += 1

    if kind == "exact_post":
        s += 4
    elif kind in {"recruiter", "uf_engineer"}:
        s += 1
    elif kind == "post_author":
        s += 3

    s += max(0.0, min(float(tavily_score or 0.0), 1.0)) * 3
    return s


def _result_to_lead(
    job: Job,
    row: dict,
    spec: SearchSpec,
    *,
    author_name: str = "",
    source_post_url: str = "",
) -> Optional[Lead]:
    url = _canonical_linkedin_url(row.get("url", ""))
    if not url or not _url_matches_kind(url, spec.kind):
        return None

    lead = Lead(
        url=url,
        title=(row.get("title") or "").strip(),
        snippet=(row.get("content") or "").strip(),
        query=spec.query,
        kind=spec.kind,
        role_bucket=spec.role_bucket,
    )
    lead.score = _score(
        job,
        lead.title,
        lead.snippet,
        lead.url,
        row.get("score", 0.0),
        spec.kind,
    )

    hay = f"{lead.title} {lead.snippet}".lower()
    if spec.kind == "exact_post":
        req_match = bool(job.external_id and str(job.external_id).lower() in hay)
        significant = [t for t in _normalize_text(job.title).split() if len(t) >= 4]
        title_matches = sum(1 for t in significant if t in _normalize_text(hay).split())
        if not _company_matches_text(hay, job.company) or (not req_match and title_matches < 2):
            return None
        lead.author_name = _extract_post_author(lead.title, lead.url, job.company)
        lead.connection_type = "job_poster"
        lead.affiliations = _extract_affiliations(hay)
        lead.source_post_url = lead.url
        lead.relevance_reason = (
            "requisition ID matched exact LinkedIn post"
            if req_match else "job title matched exact LinkedIn post"
        )
    elif spec.kind == "recruiter":
        if not _company_matches_text(hay, job.company) or not any(t in hay for t in _RECRUITER_TERMS):
            return None
        lead.author_name = _extract_profile_name(lead.title)
        lead.author_profile_url = lead.url
        lead.connection_type = "recruiter"
        lead.affiliations = _extract_affiliations(hay)
        lead.relevance_reason = "profile contains recruiting or university-talent evidence"
    elif spec.kind == "uf_engineer":
        if "university of florida" not in hay and " uf " not in f" {hay} ":
            return None
        if not any(t in hay for t in ("engineer", "engineering", "hardware", "firmware", "software", "silicon", "verification")):
            return None
        lead.author_name = _extract_profile_name(lead.title)
        lead.author_profile_url = lead.url
        lead.connection_type = "technical_connection"
        lead.affiliations = tuple(dict.fromkeys(("UF", *_extract_affiliations(hay))))
        lead.relevance_reason = "UF-affiliated technical profile"
    elif spec.kind == "post_author":
        author_tokens = [token for token in _normalize_text(author_name).split() if len(token) >= 2]
        hay_tokens = set(_normalize_text(hay).split())
        name_matches = sum(1 for token in author_tokens if token in hay_tokens)
        if (
            not author_tokens
            or name_matches < min(2, len(author_tokens))
            or not _company_matches_text(hay, job.company)
        ):
            return None
        lead.author_name = author_name
        lead.author_profile_url = lead.url
        lead.connection_type = _connection_type(hay)
        lead.affiliations = _extract_affiliations(hay)
        lead.source_post_url = source_post_url
        lead.relevance_reason = (
            f"profile matches exact-post author and {job.company}; "
            f"classified as {lead.connection_type.replace('_', ' ')}"
        )

    if "UF" in lead.affiliations:
        lead.score += 4
    if "PIKE" in lead.affiliations:
        lead.score += 3
    if lead.connection_type == "recruiter":
        lead.score += 4
    elif lead.connection_type == "technical_connection":
        lead.score += 2

    minimum = {
        "exact_post": 10.0,
        "recruiter": 5.0,
        "uf_engineer": 7.0,
        "post_author": 6.0,
    }.get(spec.kind, 5.0)
    return lead if lead.score >= minimum else None


def _dedupe_leads(leads: List[Lead]) -> List[Lead]:
    ordered = sorted(leads, key=lambda x: x.score, reverse=True)
    out: List[Lead] = []
    seen_urls = set()
    for lead in ordered:
        canonical = _canonical_linkedin_url(lead.url)
        if canonical and canonical in seen_urls:
            continue
        if canonical:
            seen_urls.add(canonical)
        out.append(lead)
    return out


def _merge_lead(found: dict[str, Lead], lead: Lead) -> None:
    existing = found.get(lead.url)
    if existing is None:
        found[lead.url] = lead
        return

    winner, other = (lead, existing) if lead.score > existing.score else (existing, lead)
    winner.score = max(winner.score, other.score)
    winner.affiliations = tuple(dict.fromkeys((*winner.affiliations, *other.affiliations)))
    winner.author_name = winner.author_name or other.author_name
    winner.author_profile_url = winner.author_profile_url or other.author_profile_url
    winner.source_post_url = winner.source_post_url or other.source_post_url
    winner.relevance_reason = winner.relevance_reason or other.relevance_reason
    if _CONNECTION_PRIORITY.get(other.connection_type, 0) > _CONNECTION_PRIORITY.get(winner.connection_type, 0):
        winner.connection_type = other.connection_type
    found[lead.url] = winner


def _balanced_leads(leads: List[Lead]) -> List[Lead]:
    """Keep job posts and recruiter/profile leads from crowding each other out."""
    ordered = _dedupe_leads(leads)
    max_total = max(1, int(os.getenv("TAVILY_MAX_LEADS_PER_JOB", "14")))
    quotas = {
        "exact_post": 4,
        "post_author": 4,
        "recruiter": 6,
        "uf_engineer": 3,
        "other": 2,
    }
    selected = []
    selected_urls = set()
    for kind in ("exact_post", "post_author", "recruiter", "uf_engineer", "other"):
        rows = [lead for lead in ordered if lead.kind == kind][:quotas[kind]]
        for lead in rows:
            if lead.url not in selected_urls and len(selected) < max_total:
                selected.append(lead)
                selected_urls.add(lead.url)
    for lead in ordered:
        if lead.url not in selected_urls and len(selected) < max_total:
            selected.append(lead)
            selected_urls.add(lead.url)
    return selected


def search_linkedin_public_index_outcome(
    job: Job,
    max_per_query: int = 5,
    *,
    db=None,
    budget: Optional[TavilyBudget] = None,
    client=None,
    kinds: Optional[Iterable[str]] = None,
) -> EnrichmentOutcome:
    """Search Tavily's public index for LinkedIn posts/profiles with credit guards.

    Search behavior:
      * basic depth only (1 credit per uncached request),
      * company-level profile searches cached for 14 days,
      * exact-post searches cached for 18 hours,
      * up to two cached exact-post author profile lookups per job by default,
      * hard per-run budget + monthly reserve,
      * fail closed if Tavily usage cannot be verified (default).
    """
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return EnrichmentOutcome([], False, 0, "Tavily key not configured")

    if budget is None:
        budget = TavilyBudget(key)

    if client is None:
        from tavily import TavilyClient
        client = TavilyClient(api_key=key)

    max_queries = int(os.getenv("TAVILY_MAX_QUERIES", "2"))
    max_queries = max(0, min(max_queries, 3))
    specs = selected_search_specs(job, kinds)[:max_queries]
    if max_queries == 0 or not specs:
        return EnrichmentOutcome([], True, 0, "")

    found = {}
    completed = True
    start_spent = budget.spent_this_run

    def run_spec(
        spec: SearchSpec,
        *,
        author_name: str = "",
        source_post_url: str = "",
    ) -> bool:
        nonlocal completed
        rows = None
        if db is not None:
            rows = db.get_cached_search(spec.cache_key, spec.ttl_hours)

        if rows is None:
            if not budget.allow_search():
                completed = False
                return False
            # Count conservatively before the request. If the network fails after
            # Tavily accepted it, the guard still treats the credit as spent.
            budget.record_search()
            if db is not None and hasattr(db, "record_tavily_credit"):
                db.record_tavily_credit(job.company, spec.kind, spec.cache_key)
            response = client.search(
                query=spec.query,
                search_depth=spec.search_depth,
                max_results=max(1, min(int(spec.max_results or max_per_query), 10)),
                include_domains=list(spec.include_domains),
                country=spec.country,
                include_answer=False,
                include_raw_content=False,
                auto_parameters=False,
                include_usage=True,
            )
            rows = response.get("results", []) if isinstance(response, dict) else []
            if db is not None:
                db.put_cached_search(spec.cache_key, rows)

        for row in rows or []:
            if not isinstance(row, dict):
                continue
            lead = _result_to_lead(
                job,
                row,
                spec,
                author_name=author_name,
                source_post_url=source_post_url,
            )
            if lead is None:
                continue
            _merge_lead(found, lead)
        return True

    author_lookup_enabled = os.getenv("TAVILY_AUTO_AUTHOR_LOOKUP", "true").lower() not in {
        "0", "false", "no",
    }
    max_author_lookups = max(0, int(os.getenv("TAVILY_MAX_AUTHOR_LOOKUPS_PER_JOB", "2")))

    for spec in specs:
        if not run_spec(spec):
            break
        if spec.kind != "exact_post" or not author_lookup_enabled or max_author_lookups == 0:
            continue

        exact_posts = sorted(
            (
                lead for lead in found.values()
                if lead.kind == "exact_post" and lead.author_name
            ),
            key=lambda lead: lead.score,
            reverse=True,
        )
        seen_authors = set()
        for post in exact_posts:
            author_key = _normalize_text(post.author_name)
            if not author_key or author_key in seen_authors:
                continue
            seen_authors.add(author_key)
            author_spec = build_author_search_spec(job, post.author_name)
            if not run_spec(
                author_spec,
                author_name=post.author_name,
                source_post_url=post.url,
            ):
                break
            if len(seen_authors) >= max_author_lookups:
                break
        if not completed:
            break

    leads = _balanced_leads(list(found.values()))
    return EnrichmentOutcome(
        leads=leads,
        completed=completed,
        searches_used=budget.spent_this_run - start_spent,
        blocked_reason=budget.block_reason if not completed else "",
    )


def search_linkedin_public_index(
    job: Job,
    max_per_query: int = 5,
    *,
    db=None,
    budget: Optional[TavilyBudget] = None,
    client=None,
    kinds: Optional[Iterable[str]] = None,
) -> List[Lead]:
    """Backwards-compatible convenience wrapper returning only leads."""
    return search_linkedin_public_index_outcome(
        job, max_per_query=max_per_query, db=db, budget=budget, client=client, kinds=kinds
    ).leads
