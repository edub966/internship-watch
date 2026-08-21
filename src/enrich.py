import hashlib
import json
import os
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


@dataclass(frozen=True)
class SearchSpec:
    kind: str
    query: str
    include_domains: tuple[str, ...]
    ttl_hours: int

    @property
    def cache_key(self) -> str:
        material = json.dumps(
            {
                "v": 2,
                "kind": self.kind,
                "query": self.query,
                "domains": self.include_domains,
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
            os.getenv("TAVILY_MAX_CREDITS_PER_RUN", "10")
        )
        self.reserve_credits = reserve_credits if reserve_credits is not None else int(
            os.getenv("TAVILY_CREDIT_RESERVE", "250")
        )
        if require_usage_check is None:
            require_usage_check = os.getenv("TAVILY_REQUIRE_USAGE_CHECK", "true").lower() not in {
                "0", "false", "no"
            }
        self.require_usage_check = require_usage_check
        self.usage_getter = usage_getter or self._fetch_usage
        self.daily_credit_cap = daily_credit_cap if daily_credit_cap is not None else int(
            os.getenv("TAVILY_DAILY_CREDIT_CAP", "20")
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


def _role_terms(job: Job) -> str:
    title = (job.title or "").lower()
    groups = [
        ("hardware", ("hardware", "asic", "rtl", "verification", "silicon", "fpga", "digital")),
        ("firmware embedded", ("firmware", "embedded", "soc", "driver")),
        ("software", ("software", "sw ", "compiler", "cuda", "gpu")),
        ("machine learning", ("machine learning", " ai ", "ml ")),
    ]
    chosen = [label for label, tokens in groups if any(t in f" {title} " for t in tokens)]
    return " ".join(chosen[:2]) or "engineering"


def build_search_specs(job: Job) -> List[SearchSpec]:
    """Build domain-restricted Tavily searches without relying on Google operators.

    Only the exact-post query is job-specific. Recruiter and UF-engineer queries
    are company-level and are cached for two weeks, so ten new jobs at one company
    do not burn the same two credits ten times.
    """
    clean_title = " ".join((job.title or "").replace('"', "").split())
    company = " ".join((job.company or "").replace('"', "").split())
    req = " ".join(str(job.external_id or "").replace('"', "").split())
    role_terms = _role_terms(job)

    # Some ATS titles already begin with the employer name (for example
    # "NVIDIA 2027 Internships: Hardware ASIC Design"). Avoid sending
    # "NVIDIA NVIDIA ..." to the search API.
    if company and clean_title.lower().startswith(company.lower() + " "):
        clean_title = clean_title[len(company):].strip()

    exact_parts = [company, clean_title]
    if req:
        exact_parts.append(req)
    exact_parts.extend(["internship", "hiring"])

    return [
        SearchSpec(
            kind="exact_post",
            query=" ".join(exact_parts),
            include_domains=("linkedin.com/posts",),
            ttl_hours=18,
        ),
        SearchSpec(
            kind="recruiter",
            query=f"{company} United States university recruiting early careers talent acquisition recruiter internships",
            include_domains=("linkedin.com/in",),
            ttl_hours=24 * 14,
        ),
        SearchSpec(
            kind="uf_engineer",
            query=f"{company} University of Florida {role_terms} engineer",
            include_domains=("linkedin.com/in",),
            ttl_hours=24 * 14,
        ),
    ]


# Backwards-compatible helper used by earlier tests/local scripts.
def build_queries(job: Job) -> List[str]:
    return [spec.query for spec in build_search_specs(job)]


_ALLOWED_KINDS = {"exact_post", "recruiter", "uf_engineer"}


def selected_search_specs(job: Job, kinds: Optional[Iterable[str]] = None) -> List[SearchSpec]:
    """Return the Tavily searches permitted for this run.

    Production defaults to the reusable company-level recruiter search only.
    Exact-post and UF-engineer lookups stay available for explicit deep-enrich
    requests without burning a credit for every new posting.
    """
    if kinds is None:
        raw = os.getenv("TAVILY_AUTO_SEARCH_KINDS", "recruiter")
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
    return urlunparse(("https", "www.linkedin.com", path, "", "", ""))


def _url_matches_kind(url: str, kind: str) -> bool:
    path = urlparse(url).path.lower()
    if kind == "exact_post":
        return path.startswith("/posts/") or "/posts/" in path
    if kind in {"recruiter", "uf_engineer"}:
        return path.startswith("/in/") or "/in/" in path
    return True


def _score(job: Job, title: str, snippet: str, url: str, tavily_score: float = 0.0, kind: str = "other") -> float:
    hay = f"{title} {snippet}".lower()
    s = 0.0

    for token in job.company.lower().split():
        if token in hay:
            s += 2

    title_tokens = [t for t in job.title.lower().replace(',', ' ').split() if len(t) >= 4]
    s += min(sum(1 for t in title_tokens if t in hay), 6)

    if job.external_id and str(job.external_id).lower() in hay:
        s += 8

    if "university of florida" in hay or " uf " in f" {hay} ":
        s += 4

    for term in [
        "recruiter", "university", "early careers", "talent", "hiring",
        "intern", "engineer", "manager", "hardware", "firmware", "silicon",
        "verification", "software",
    ]:
        if term in hay:
            s += 1

    if kind == "exact_post":
        s += 4
    elif kind in {"recruiter", "uf_engineer"}:
        s += 1

    s += max(0.0, min(float(tavily_score or 0.0), 1.0)) * 3
    return s


def _result_to_lead(job: Job, row: dict, spec: SearchSpec) -> Optional[Lead]:
    url = _canonical_linkedin_url(row.get("url", ""))
    if not url or not _url_matches_kind(url, spec.kind):
        return None

    lead = Lead(
        url=url,
        title=(row.get("title") or "").strip(),
        snippet=(row.get("content") or "").strip(),
        query=spec.query,
        kind=spec.kind,
    )
    lead.score = _score(
        job,
        lead.title,
        lead.snippet,
        lead.url,
        row.get("score", 0.0),
        spec.kind,
    )

    # Avoid surfacing very weak search-engine matches just because LinkedIn was returned.
    hay = f"{lead.title} {lead.snippet}".lower()
    if spec.kind == "exact_post":
        req_match = bool(job.external_id and str(job.external_id).lower() in hay)
        significant = [t for t in job.title.lower().replace(",", " ").split() if len(t) >= 4]
        title_matches = sum(1 for t in significant if t in hay)
        if not req_match and title_matches < 2:
            return None
    elif spec.kind == "recruiter":
        if not any(t in hay for t in ("recruiter", "recruiting", "talent acquisition", "early careers")):
            return None
    elif spec.kind == "uf_engineer":
        if "university of florida" not in hay and " uf " not in f" {hay} ":
            return None
        if not any(t in hay for t in ("engineer", "engineering", "hardware", "firmware", "software", "silicon", "verification")):
            return None

    minimum = {"exact_post": 10.0, "recruiter": 5.0, "uf_engineer": 7.0}.get(spec.kind, 5.0)
    return lead if lead.score >= minimum else None


def _lead_content_signature(lead: Lead) -> str:
    # Recruiter searches often return the same public recruiting post attached
    # to several profiles. Keep one copy so Discord surfaces diverse contacts.
    text = " ".join((lead.snippet or "").lower().split())
    if not text:
        return ""
    return text[:420]


def _dedupe_leads(leads: List[Lead]) -> List[Lead]:
    ordered = sorted(leads, key=lambda x: x.score, reverse=True)
    out: List[Lead] = []
    recruiter_signatures = set()
    for lead in ordered:
        if lead.kind == "recruiter":
            sig = _lead_content_signature(lead)
            if sig and sig in recruiter_signatures:
                continue
            if sig:
                recruiter_signatures.add(sig)
        out.append(lead)
    return out


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

    max_queries = int(os.getenv("TAVILY_MAX_QUERIES", "3"))
    max_queries = max(0, min(max_queries, 3))
    specs = selected_search_specs(job, kinds)[:max_queries]
    if max_queries == 0 or not specs:
        return EnrichmentOutcome([], True, 0, "")

    found = {}
    completed = True
    start_spent = budget.spent_this_run
    for spec in specs:
        rows = None
        if db is not None:
            rows = db.get_cached_search(spec.cache_key, spec.ttl_hours)

        if rows is None:
            if not budget.allow_search():
                completed = False
                break
            # Count conservatively before the request. If the network fails after
            # Tavily accepted it, the guard still treats the credit as spent.
            budget.record_search()
            if db is not None and hasattr(db, "record_tavily_credit"):
                db.record_tavily_credit(job.company, spec.kind, spec.cache_key)
            response = client.search(
                query=spec.query,
                search_depth="basic",
                max_results=max(1, min(int(max_per_query), 10)),
                include_domains=list(spec.include_domains),
                country="united states",
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
            lead = _result_to_lead(job, row, spec)
            if lead is None:
                continue
            if lead.url not in found or lead.score > found[lead.url].score:
                found[lead.url] = lead

    leads = _dedupe_leads(list(found.values()))[:8]
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
