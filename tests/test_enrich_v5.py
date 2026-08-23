import os

from src.db import JobDB
from src.enrich import TavilyBudget, build_search_specs, search_linkedin_public_index
from src.models import Job


def _job():
    return Job(
        company="NVIDIA",
        external_id="JR12345",
        title="ASIC Design Intern",
        location="Santa Clara, CA",
        url="https://example.com/job",
        source="test",
    )


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        domains = kwargs["include_domains"]
        if domains == ["linkedin.com/posts"]:
            return {"results": [{
                "url": "https://www.linkedin.com/posts/alice_example?utm_source=x",
                "title": "NVIDIA ASIC Design Intern JR12345 hiring",
                "content": "NVIDIA team is hiring an ASIC Design Intern JR12345",
                "score": 0.95,
            }]}
        return {"results": [{
            "url": "https://www.linkedin.com/in/bob-example/?trk=x",
            "title": "Bob Example - NVIDIA Engineer",
            "content": "NVIDIA University of Florida engineer",
            "score": 0.8,
        }]}


def _usage():
    return {"key": {"usage": 100, "limit": 1000}}


def test_specs_use_domain_filters_not_site_operators():
    specs = build_search_specs(_job())
    assert len(specs) == 3
    assert specs[0].include_domains == ("linkedin.com/posts",)
    assert specs[1].include_domains == ("linkedin.com/in",)
    assert all("site:" not in s.query for s in specs)


def test_budget_caps_uncached_searches(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_MAX_QUERIES", "3")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post,recruiter,uf_engineer")
    db = JobDB(str(tmp_path / "x.db"))
    client = FakeClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=2, reserve_credits=250,
        require_usage_check=True, usage_getter=_usage,
    )
    leads = search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert len(client.calls) == 2
    assert budget.spent_this_run == 2
    assert leads
    db.close()


def test_company_searches_are_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_MAX_QUERIES", "3")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post,recruiter,uf_engineer")
    db = JobDB(str(tmp_path / "x.db"))
    client = FakeClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=10, reserve_credits=250,
        require_usage_check=True, usage_getter=_usage,
    )
    search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert len(client.calls) == 3

    # Same job within cache TTL should cost zero more search calls.
    search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert len(client.calls) == 3
    assert budget.spent_this_run == 3
    db.close()


def test_reserve_blocks_search(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    db = JobDB(str(tmp_path / "x.db"))
    client = FakeClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=10, reserve_credits=250,
        require_usage_check=True,
        usage_getter=lambda: {"key": {"usage": 760, "limit": 1000}},
    )
    leads = search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert leads == []
    assert client.calls == []
    assert "reserve" in budget.block_reason.lower()
    db.close()


def test_non_linkedin_and_wrong_path_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_MAX_QUERIES", "1")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post")

    class BadClient:
        def search(self, **kwargs):
            return {"results": [
                {"url": "https://evil.com/linkedin.com/posts/x", "title": "NVIDIA JR12345", "content": "hiring", "score": 1},
                {"url": "https://www.linkedin.com/in/not-a-post", "title": "NVIDIA JR12345", "content": "hiring", "score": 1},
            ]}

    db = JobDB(str(tmp_path / "x.db"))
    budget = TavilyBudget("tvly-test", 1, 0, True, _usage)
    assert search_linkedin_public_index(_job(), db=db, budget=budget, client=BadClient()) == []
    db.close()


def test_usage_guard_falls_back_to_account_plan_when_key_limit_is_null():
    budget = TavilyBudget(
        "tvly-test",
        max_credits_per_run=1,
        reserve_credits=250,
        require_usage_check=True,
        usage_getter=lambda: {
            "key": {"usage": 15, "limit": None, "search_usage": 15},
            "account": {"current_plan": "Researcher", "plan_usage": 15, "plan_limit": 1000},
        },
    )
    assert budget.check() is True
    assert budget.usage_source == "account plan"
    assert budget.remaining_before_search == 985
    assert budget.allow_search() is True


def test_usage_guard_prefers_real_key_cap_when_present():
    budget = TavilyBudget(
        "tvly-test",
        max_credits_per_run=1,
        reserve_credits=100,
        require_usage_check=True,
        usage_getter=lambda: {
            "key": {"usage": 125, "limit": 500},
            "account": {"plan_usage": 300, "plan_limit": 1000},
        },
    )
    assert budget.check() is True
    assert budget.usage_source == "API key"
    assert budget.remaining_before_search == 375


def test_usage_guard_fails_closed_when_both_limits_are_unusable():
    budget = TavilyBudget(
        "tvly-test",
        max_credits_per_run=1,
        reserve_credits=250,
        require_usage_check=True,
        usage_getter=lambda: {
            "key": {"usage": 15, "limit": None},
            "account": {"plan_usage": 15, "plan_limit": None},
        },
    )
    assert budget.check() is False
    assert "usage endpoint did not provide" in budget.block_reason


def test_exact_query_does_not_duplicate_company_prefix():
    job = Job(
        company="NVIDIA",
        external_id="JR2023486",
        title="NVIDIA 2027 Internships: Hardware ASIC Design",
        location="US, CA, Santa Clara",
        url="https://example.com/job",
        source="workday",
    )
    specs = build_search_specs(job)
    assert specs[0].query.startswith("NVIDIA 2027 Internships: Hardware ASIC Design")
    assert "NVIDIA NVIDIA" not in specs[0].query
    assert "United States" in specs[1].query


def test_default_auto_policy_is_recruiter_only(monkeypatch):
    monkeypatch.delenv("TAVILY_AUTO_SEARCH_KINDS", raising=False)
    from src.enrich import selected_search_specs
    specs = selected_search_specs(_job())
    assert [s.kind for s in specs] == ["recruiter"]


def test_manual_kinds_can_request_exact_without_uf():
    from src.enrich import selected_search_specs
    specs = selected_search_specs(_job(), ["exact_post", "recruiter"])
    assert [s.kind for s in specs] == ["exact_post", "recruiter"]


def test_local_daily_cap_blocks_even_if_remote_usage_lags(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "recruiter")
    db = JobDB(str(tmp_path / "x.db"))
    for _ in range(3):
        db.record_tavily_credit("NVIDIA", "recruiter", "x")
    client = FakeClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=10, reserve_credits=0,
        require_usage_check=True, usage_getter=_usage,
        daily_credit_cap=3, local_daily_usage_getter=db.tavily_credits_used_today,
    )
    leads = search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert leads == []
    assert client.calls == []
    assert "rolling-24h" in budget.block_reason.lower()
    db.close()


def test_paid_attempt_is_written_to_local_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "recruiter")
    db = JobDB(str(tmp_path / "x.db"))
    client = FakeClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=2, reserve_credits=0,
        require_usage_check=True, usage_getter=_usage,
        daily_credit_cap=20, local_daily_usage_getter=db.tavily_credits_used_today,
    )
    search_linkedin_public_index(_job(), db=db, budget=budget, client=client)
    assert db.tavily_credits_used_today() == 1
    db.close()


def test_role_bucketed_recruiter_queries_are_distinct_and_specific(monkeypatch):
    monkeypatch.delenv("TAVILY_AUTO_SEARCH_KINDS", raising=False)
    specs = build_search_specs(_job())
    recruiter = next(s for s in specs if s.kind == "recruiter")
    assert recruiter.role_bucket == "hardware"
    assert "ASIC" in recruiter.query or "silicon" in recruiter.query.lower()
    assert "university recruiter" not in recruiter.query.lower()


def test_recruiter_dedupe_preserves_distinct_profile_urls(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "recruiter")

    class DuplicateRecruiterClient:
        def search(self, **kwargs):
            content = "NVIDIA University Recruiting is growing. Early careers recruiter hiring interns."
            return {"results": [
                {"url": "https://www.linkedin.com/in/a", "title": "A Recruiter", "content": content, "score": .8},
                {"url": "https://www.linkedin.com/in/b", "title": "B Recruiter", "content": content, "score": .7},
                {"url": "https://www.linkedin.com/in/c", "title": "C Recruiter", "content": "NVIDIA early careers recruiter for hardware interns", "score": .6},
            ]}

    db = JobDB(str(tmp_path / "x.db"))
    budget = TavilyBudget("tvly-test", 2, 0, True, _usage, daily_credit_cap=20,
                          local_daily_usage_getter=db.tavily_credits_used_today)
    leads = search_linkedin_public_index(_job(), db=db, budget=budget, client=DuplicateRecruiterClient())
    assert len(leads) == 3
    assert {lead.url for lead in leads} == {"https://www.linkedin.com/in/a", "https://www.linkedin.com/in/b", "https://www.linkedin.com/in/c"}
    db.close()
