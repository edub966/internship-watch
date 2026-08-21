import yaml

import src.main as main
from src.db import JobDB
from src.enrich import EnrichmentOutcome, Lead
from src.models import Job


class FakeSource:
    last_scan_note = ""
    def fetch(self):
        return [Job(
            company="NVIDIA",
            external_id="JRNEW",
            title="ASIC Design Intern",
            location="Santa Clara, CA",
            url="https://example.com/JRNEW",
            source="fake",
            posted_at="2026-08-21",
            description="ASIC RTL verification internship",
        )]


class FakeBudget:
    def __init__(self, key, **kwargs):
        self.spent_this_run = 0
        self.remaining_before_search = 900
        self.block_reason = ""


def _config(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(yaml.safe_dump({"companies": [{
        "name": "NVIDIA", "enabled": True, "type": "workday",
        "host": "x", "tenant": "x", "site": "x", "us_only": True,
    }]}))
    return str(p)


def test_end_to_end_queue_enrich_alert_and_mark_sent(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(main, "build_source", lambda cfg: FakeSource())
    monkeypatch.setattr(main, "TavilyBudget", FakeBudget)

    calls = {"enrich": 0, "alert": 0}
    def fake_enrich(job, db=None, budget=None):
        calls["enrich"] += 1
        return EnrichmentOutcome([Lead(
            url="https://www.linkedin.com/posts/a",
            title="Hiring JRNEW",
            snippet="NVIDIA ASIC intern",
            query="q",
            kind="exact_post",
            score=20,
        )], completed=True, searches_used=1)
    def fake_alert(job, leads, enrichment_note=""):
        calls["alert"] += 1
        assert leads and leads[0].kind == "exact_post"

    monkeypatch.setattr(main, "search_linkedin_public_index_outcome", fake_enrich)
    monkeypatch.setattr(main, "discord_alert", fake_alert)

    main.run(_config(tmp_path), 12.0, enrich=True, seed=False)
    assert calls == {"enrich": 1, "alert": 1}

    db = JobDB(str(db_path))
    assert db.pending_alert_count() == 0
    assert db.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    db.close()

    # Same requisition on the next scan must not alert again.
    main.run(_config(tmp_path), 12.0, enrich=True, seed=False)
    assert calls == {"enrich": 1, "alert": 1}


def test_failed_discord_remains_queued(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(main, "build_source", lambda cfg: FakeSource())

    def fail_alert(job, leads, enrichment_note=""):
        raise RuntimeError("discord unavailable")

    monkeypatch.setattr(main, "discord_alert", fail_alert)
    main.run(_config(tmp_path), 12.0, enrich=False, seed=False)

    db = JobDB(str(db_path))
    assert db.pending_alert_count() == 1
    err = db.conn.execute("SELECT last_error FROM alert_queue").fetchone()[0]
    assert "discord unavailable" in err
    db.close()


def test_deferred_networking_survives_and_sends_followup(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(main, "build_source", lambda cfg: FakeSource())
    monkeypatch.setattr(main, "TavilyBudget", FakeBudget)

    state = {"round": 0, "alerts": 0, "followups": 0}
    def staged_enrich(job, db=None, budget=None):
        state["round"] += 1
        if state["round"] == 1:
            return EnrichmentOutcome([], completed=False, searches_used=0, blocked_reason="budget cap")
        return EnrichmentOutcome([Lead(
            url="https://www.linkedin.com/in/later",
            title="NVIDIA University Recruiter",
            snippet="University recruiting at NVIDIA",
            query="q",
            kind="recruiter",
            score=10,
        )], completed=True, searches_used=1)

    monkeypatch.setattr(main, "search_linkedin_public_index_outcome", staged_enrich)
    monkeypatch.setattr(main, "discord_alert", lambda *a, **k: state.__setitem__("alerts", state["alerts"] + 1))
    monkeypatch.setattr(main, "discord_networking_followup", lambda *a, **k: state.__setitem__("followups", state["followups"] + 1))

    cfg = _config(tmp_path)
    main.run(cfg, 12.0, enrich=True, seed=False)
    assert state["alerts"] == 1
    assert state["followups"] == 0

    db = JobDB(str(db_path))
    assert db.pending_alert_count() == 0
    assert not db.networking_is_complete(_job_for_test())
    db.close()

    main.run(cfg, 12.0, enrich=True, seed=False)
    assert state["alerts"] == 1  # no duplicate internship alert
    assert state["followups"] == 1

    db = JobDB(str(db_path))
    assert db.networking_is_complete(_job_for_test())
    db.close()


def _job_for_test():
    return Job(
        company="NVIDIA", external_id="JRNEW", title="ASIC Design Intern",
        location="Santa Clara, CA", url="https://example.com/JRNEW", source="fake"
    )


def test_non_us_job_never_enriches_or_alerts(monkeypatch, tmp_path):
    class ForeignSource:
        last_scan_note = ""
        def fetch(self):
            return [Job(
                company="NVIDIA", external_id="JRFOREIGN", title="ASIC Design Intern",
                location="Shanghai, Shanghai, CN", url="https://x", source="workday",
                description="ASIC RTL verification internship",
            )]

    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(main, "build_source", lambda cfg: ForeignSource())
    monkeypatch.setattr(main, "TavilyBudget", FakeBudget)

    calls = {"enrich": 0, "alert": 0}
    monkeypatch.setattr(main, "search_linkedin_public_index_outcome", lambda *a, **k: calls.__setitem__("enrich", calls["enrich"] + 1))
    monkeypatch.setattr(main, "discord_alert", lambda *a, **k: calls.__setitem__("alert", calls["alert"] + 1))

    main.run(_config(tmp_path), 12.0, enrich=True, seed=False)
    assert calls == {"enrich": 0, "alert": 0}

    db = JobDB(str(db_path))
    assert db.conn.execute("SELECT COUNT(*) FROM jobs WHERE external_id='JRFOREIGN'").fetchone()[0] == 1
    assert db.pending_alert_count() == 0
    assert db.conn.execute("SELECT COUNT(*) FROM networking_queue").fetchone()[0] == 0
    db.close()


def test_ambiguous_location_never_spends_tavily(monkeypatch, tmp_path):
    class AmbiguousSource:
        last_scan_note = ""
        def fetch(self):
            return [Job(
                company="NVIDIA", external_id="JRAMB", title="Firmware Intern",
                location="Remote", url="https://x", source="workday",
                description="embedded firmware internship",
            )]

    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(main, "build_source", lambda cfg: AmbiguousSource())

    class ExplodingBudget:
        def __init__(self, key):
            raise AssertionError("TavilyBudget must not even be constructed for an unqueued ambiguous job")

    # Budget object may still be constructed globally when a key is configured,
    # so instead guard the actual enrichment call, which is the paid path.
    monkeypatch.setattr(main, "TavilyBudget", FakeBudget)
    monkeypatch.setattr(main, "search_linkedin_public_index_outcome", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Tavily search")))
    monkeypatch.setattr(main, "discord_alert", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no alert")))

    main.run(_config(tmp_path), 12.0, enrich=True, seed=False)


def test_seed_baselines_eligibility_then_location_transition_alerts_once(monkeypatch, tmp_path):
    state = {"location": "Remote"}

    class MovingSource:
        last_scan_note = ""
        def fetch(self):
            return [Job(
                company="NVIDIA", external_id="JRMOVE", title="ASIC Design Intern",
                location=state["location"], url="https://x", source="workday",
                description="ASIC RTL verification internship",
            )]

    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(main, "build_source", lambda cfg: MovingSource())

    cfg = _config(tmp_path)
    main.run(cfg, 12.0, enrich=False, seed=True)

    state["location"] = "Santa Clara, CA"
    sent = {"n": 0}
    monkeypatch.setattr(main, "discord_alert", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
    main.run(cfg, 12.0, enrich=False, seed=False)
    assert sent["n"] == 1

    main.run(cfg, 12.0, enrich=False, seed=False)
    assert sent["n"] == 1
