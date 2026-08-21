from src.db import JobDB
from src.models import Job


def _job():
    return Job("NVIDIA", "JR1", "ASIC Intern", "CA", "https://x", "test", score=20)


def test_alert_queue_retries_until_marked_sent(tmp_path):
    db = JobDB(str(tmp_path / "jobs.db"))
    db.sync_company("NVIDIA", [_job()])
    db.enqueue_alert(_job())
    assert db.pending_alert_count() == 1
    assert len(db.pending_alert_jobs()) == 1

    db.mark_alert_error(_job(), "discord down")
    assert db.pending_alert_count() == 1

    db.mark_alert_sent(_job())
    assert db.pending_alert_count() == 0
    db.close()


def test_sqlite_state_is_single_file_not_wal(tmp_path):
    path = tmp_path / "jobs.db"
    db = JobDB(str(path))
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode == "delete"
    db.sync_company("NVIDIA", [_job()])
    db.close()
    assert path.exists()
    assert not (tmp_path / "jobs.db-wal").exists()
