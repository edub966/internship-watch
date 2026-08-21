from src.db import JobDB
from src.models import Job


def _job(jid, title="Intern", score=0):
    return Job(
        company="Qualcomm",
        external_id=str(jid),
        title=title,
        location="San Diego, CA",
        url=f"https://example.com/{jid}",
        source="eightfold",
        score=score,
    )


def test_sync_stores_irrelevant_jobs_so_they_are_not_new_later(tmp_path):
    db = JobDB(str(tmp_path / "jobs.db"))

    new, disappeared = db.sync_company("Qualcomm", [_job(1), _job(2)])
    assert {j.external_id for j in new} == {"1", "2"}
    assert disappeared == 0

    # Same requisitions on the next run are not new, regardless of filtering.
    new, disappeared = db.sync_company("Qualcomm", [_job(1), _job(2)])
    assert new == []
    assert disappeared == 0


def test_sync_marks_missing_postings_inactive(tmp_path):
    db = JobDB(str(tmp_path / "jobs.db"))
    db.sync_company("Qualcomm", [_job(1), _job(2)])

    new, disappeared = db.sync_company("Qualcomm", [_job(2), _job(3)])
    assert {j.external_id for j in new} == {"3"}
    assert disappeared == 1

    rows = dict(db.conn.execute(
        "SELECT external_id, is_active FROM jobs WHERE company='Qualcomm'"
    ).fetchall())
    assert rows == {"1": 0, "2": 1, "3": 1}
