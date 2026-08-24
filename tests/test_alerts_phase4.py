import yaml

import src.main as main
from src.alerts import _lead_section, format_match_details
from src.db import JobDB
from src.enrich import Lead
from src.models import Job


def _job(title, description, score=88):
    return Job(
        company="ExampleCo",
        external_id="REQ-1",
        title=title,
        location="Austin, TX, United States",
        url="https://example.com/job",
        source="test",
        description=description,
        score=score,
    )


def test_alert_details_recommend_hardware_resume_and_show_all_tracks():
    details = format_match_details(_job(
        "ASIC Design Undergrad Intern 2027",
        "Bachelor's students working on RTL, SystemVerilog, digital design, and computer architecture.",
    ))
    assert "Eligibility: ✅ Eligible" in details
    assert "Primary track: Hardware / Architecture" in details
    assert "Recommended resume: Hardware" in details
    assert "Hardware / Architecture:" in details
    assert "SWE / Systems:" in details
    assert "Data / ML / AI:" in details


def test_alert_details_mark_missing_degree_information_for_verification():
    details = format_match_details(_job(
        "Machine Learning Intern 2027",
        "Python, PyTorch, deep learning, and model evaluation.",
    ))
    assert "Eligibility: ⚠️ Verify" in details
    assert "Reason: degree requirement not explicit" in details
    assert "Primary track: Data / ML / AI" in details
    assert "Recommended resume: Data/ML" in details


def test_graduate_only_job_never_alerts_or_triggers_enrichment(monkeypatch, tmp_path):
    class GraduateOnlySource:
        last_scan_note = ""

        def fetch(self):
            return [_job(
                "Hardware Technologies Masters Engineering Internships",
                "MS students only. ASIC RTL computer architecture and SystemVerilog internship.",
            )]

    config_path = tmp_path / "companies.yaml"
    config_path.write_text(yaml.safe_dump({"companies": [{
        "name": "ExampleCo",
        "enabled": True,
        "type": "workday",
        "host": "x",
        "tenant": "x",
        "site": "x",
        "target_year": 2027,
        "us_only": True,
    }]}))
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(main, "build_source", lambda config: GraduateOnlySource())
    monkeypatch.setattr(
        main,
        "search_linkedin_public_index_outcome",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no Tavily search")),
    )
    monkeypatch.setattr(
        main,
        "discord_alert",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no Discord alert")),
    )

    main.run(str(config_path), 12.0, enrich=True, seed=False)

    db = JobDB(str(db_path))
    stored_score = db.conn.execute(
        "SELECT score FROM jobs WHERE company='ExampleCo' AND external_id='REQ-1'"
    ).fetchone()[0]
    assert stored_score == -50
    assert db.pending_alert_count() == 0
    assert db.conn.execute("SELECT COUNT(*) FROM networking_queue").fetchone()[0] == 0
    db.close()


def test_networking_alert_distinguishes_recruiter_author_and_affiliations():
    lead = Lead(
        url="https://www.linkedin.com/in/alice-example",
        title="Alice Example - University Recruiter at NVIDIA",
        snippet="",
        query="q",
        kind="post_author",
        author_name="Alice Example",
        connection_type="recruiter",
        affiliations=("UF", "PIKE"),
        source_post_url="https://www.linkedin.com/posts/alice-example_activity-1",
    )
    section = _lead_section([lead])
    assert "People who posted this job" in section
    assert "Alice Example" in section
    assert "Recruiter · UF · PIKE" in section
    assert "Posted: https://www.linkedin.com/posts/" in section
