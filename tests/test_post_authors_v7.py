import sqlite3

from src.db import JobDB
from src.enrich import (
    Lead,
    TavilyBudget,
    _balanced_leads,
    _company_matches_text,
    _connection_type,
    _extract_affiliations,
    _extract_post_author,
    _result_to_lead,
    build_author_search_spec,
    build_search_specs,
    search_linkedin_public_index_outcome,
)
from src.models import Job


def _job():
    return Job(
        company="NVIDIA",
        external_id="JR12345",
        title="ASIC Design Intern",
        location="Santa Clara, CA, United States",
        url="https://example.com/job",
        source="test",
    )


def _usage():
    return {"account": {"plan_usage": 35, "plan_limit": 1000}}


class AuthorClient:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]
        if kwargs["include_domains"] == ["linkedin.com/posts"]:
            return {"results": [{
                "url": "https://www.linkedin.com/posts/alice-example_activity-123",
                "title": "Alice Example's Post | LinkedIn",
                "content": "NVIDIA is hiring ASIC Design Intern JR12345.",
                "score": 0.98,
            }]}
        if query.startswith("Alice Example "):
            return {"results": [{
                "url": "https://www.linkedin.com/in/alice-example",
                "title": "Alice Example - University Recruiter at NVIDIA",
                "content": (
                    "NVIDIA university recruiting and early careers. "
                    "University of Florida alum and Pi Kappa Alpha alumnus."
                ),
                "score": 0.95,
            }]}
        return {"results": [{
            "url": "https://www.linkedin.com/in/carol-recruiter",
            "title": "Carol Recruiter - NVIDIA Talent Acquisition",
            "content": "NVIDIA university recruiter for hardware internships.",
            "score": 0.9,
        }]}


def test_exact_post_author_is_resolved_classified_and_affiliation_tagged(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post,recruiter")
    monkeypatch.setenv("TAVILY_AUTO_AUTHOR_LOOKUP", "true")
    monkeypatch.setenv("TAVILY_MAX_AUTHOR_LOOKUPS_PER_JOB", "1")
    monkeypatch.setenv("TAVILY_MAX_QUERIES", "2")
    db = JobDB(str(tmp_path / "jobs.db"))
    client = AuthorClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=20, reserve_credits=100,
        require_usage_check=True, usage_getter=_usage,
    )

    outcome = search_linkedin_public_index_outcome(
        _job(), db=db, budget=budget, client=client,
    )

    assert outcome.completed
    assert outcome.searches_used == 3
    assert len(client.calls) == 3
    exact = next(lead for lead in outcome.leads if lead.kind == "exact_post")
    alice = next(lead for lead in outcome.leads if lead.author_name == "Alice Example" and lead.kind == "post_author")
    assert exact.connection_type == "job_poster"
    assert alice.connection_type == "recruiter"
    assert alice.affiliations == ("UF", "PIKE")
    assert alice.author_profile_url.endswith("/alice-example")
    assert alice.source_post_url == exact.url
    assert "exact-post author" in alice.relevance_reason
    assert client.calls[0]["max_results"] == 10

    for lead in outcome.leads:
        db.add_lead_record(_job(), lead)
    restored = db.get_leads(_job())
    restored_alice = next(lead for lead in restored if lead.author_name == "Alice Example")
    assert restored_alice.connection_type == "recruiter"
    assert restored_alice.affiliations == ("UF", "PIKE")
    assert restored_alice.source_post_url == exact.url
    db.close()


def test_default_policy_resolves_two_distinct_exact_post_authors(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("TAVILY_AUTO_SEARCH_KINDS", "exact_post,recruiter")
    monkeypatch.setenv("TAVILY_MAX_QUERIES", "2")
    monkeypatch.delenv("TAVILY_MAX_AUTHOR_LOOKUPS_PER_JOB", raising=False)

    class MultipleAuthorClient:
        def __init__(self):
            self.calls = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["include_domains"] == ["linkedin.com/posts"]:
                return {"results": [
                    {
                        "url": f"https://www.linkedin.com/posts/{name.lower()}-example_activity-{index}",
                        "title": f"{name} Example's Post | LinkedIn",
                        "content": "NVIDIA ASIC Design Intern JR12345 is hiring.",
                        "score": 1 - index / 100,
                    }
                    for index, name in enumerate(("Alice", "Bob", "Carol"), start=1)
                ]}
            query = kwargs["query"]
            if query.startswith(("Alice Example ", "Bob Example ", "Carol Example ")):
                name = query.split()[0]
                return {"results": [{
                    "url": f"https://www.linkedin.com/in/{name.lower()}-example",
                    "title": f"{name} Example - Engineer at NVIDIA",
                    "content": f"{name} Example works in NVIDIA engineering.",
                    "score": 0.9,
                }]}
            return {"results": []}

    db = JobDB(str(tmp_path / "jobs.db"))
    client = MultipleAuthorClient()
    budget = TavilyBudget(
        "tvly-test", max_credits_per_run=20, reserve_credits=100,
        require_usage_check=True, usage_getter=_usage,
    )
    outcome = search_linkedin_public_index_outcome(_job(), db=db, budget=budget, client=client)

    resolved = {lead.author_name for lead in outcome.leads if lead.kind == "post_author"}
    assert resolved == {"Alice Example", "Bob Example"}
    assert len(client.calls) == 4  # exact post + two author profiles + recruiter
    assert not any(call["query"].startswith("Carol Example ") for call in client.calls)
    db.close()


def test_author_extraction_supports_title_and_activity_slug():
    assert _extract_post_author(
        "Jane Doe’s Post | LinkedIn",
        "https://www.linkedin.com/posts/unrelated_activity-1",
        "NVIDIA",
    ) == "Jane Doe"
    assert _extract_post_author(
        "NVIDIA ASIC internship announcement",
        "https://www.linkedin.com/posts/john-smith_activity-2",
        "NVIDIA",
    ) == "John Smith"


def test_connection_classification_is_evidence_based():
    assert _connection_type("University Recruiter, early careers") == "recruiter"
    assert _connection_type("ASIC verification engineer at NVIDIA") == "technical_connection"
    assert _connection_type("Program operations at NVIDIA") == "potential_connection"
    assert _extract_affiliations(
        "University of Florida alum; Pi Kappa Alpha alumnus"
    ) == ("UF", "PIKE")


def test_profile_relevance_requires_explicit_target_company_evidence():
    assert _company_matches_text("University Recruiter at NVIDIA, Inc.", "NVIDIA")
    assert not _company_matches_text("University Recruiter at AMD", "NVIDIA")

    unrelated = {
        "url": "https://www.linkedin.com/in/alice-example",
        "title": "Alice Example - University Recruiter at AMD",
        "content": "Alice Example recruits hardware interns at AMD.",
        "score": 1,
    }
    assert _result_to_lead(_job(), unrelated, build_search_specs(_job())[1]) is None
    assert _result_to_lead(
        _job(),
        unrelated,
        build_author_search_spec(_job(), "Alice Example"),
        author_name="Alice Example",
        source_post_url="https://www.linkedin.com/posts/alice-example_activity-1",
    ) is None


def test_balanced_results_preserve_recruiters_when_posts_score_higher(monkeypatch):
    monkeypatch.setenv("TAVILY_MAX_LEADS_PER_JOB", "8")
    leads = [
        Lead(
            url=f"https://www.linkedin.com/posts/post-{index}",
            title=f"Post {index}", snippet="", query="q", kind="exact_post",
            score=100 - index, connection_type="job_poster",
        )
        for index in range(10)
    ]
    leads.extend([
        Lead(
            url="https://www.linkedin.com/in/recruiter-a", title="Recruiter A",
            snippet="", query="q", kind="recruiter", score=5,
            connection_type="recruiter",
        ),
        Lead(
            url="https://www.linkedin.com/in/recruiter-b", title="Recruiter B",
            snippet="", query="q", kind="recruiter", score=4,
            connection_type="recruiter",
        ),
    ])
    selected = _balanced_leads(leads)
    assert len(selected) == 8
    assert sum(lead.kind == "exact_post" for lead in selected) <= 6
    assert {lead.title for lead in selected if lead.kind == "recruiter"} == {
        "Recruiter A", "Recruiter B",
    }


def test_legacy_lead_table_migrates_author_metadata_columns(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE leads (
            company TEXT NOT NULL,
            external_id TEXT NOT NULL,
            result_url TEXT NOT NULL,
            title TEXT,
            snippet TEXT,
            query TEXT,
            kind TEXT DEFAULT 'other',
            discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (company, external_id, result_url)
        )
        """
    )
    connection.commit()
    connection.close()

    db = JobDB(str(path))
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(leads)")}
    assert {
        "score", "author_name", "author_profile_url", "connection_type",
        "affiliations", "source_post_url", "relevance_reason",
    } <= columns
    db.close()
