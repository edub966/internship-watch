from unittest.mock import patch

from src.sources.eightfold import EightfoldSource


class FakeResponse:
    def __init__(self, positions, total):
        self._positions = positions
        self._total = total

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"positions": self._positions, "count": self._total}}


def pos(i):
    return {
        "id": str(100000 + i),
        "displayJobId": str(3000000 + i),
        "name": f"Intern {i}",
        "positionUrl": f"/careers/job/{100000 + i}",
    }


def test_shifted_repair_recovers_job_lost_to_duplicate_boundary():
    # Simulates the observed Qualcomm pattern: total=57, but start=50 repeats
    # one row from the previous page and therefore the normal pass has 56
    # unique jobs. A nearby/shifted request exposes the missing final job.
    all_positions = [pos(i) for i in range(57)]

    def fake_get(self, url, params=None, timeout=None):
        start = int(params["start"])
        if start == 50:
            # 7 returned rows, but one repeats position 49 and position 56 is
            # omitted. This is exactly the dangerous count/unique mismatch.
            return FakeResponse([all_positions[49]] + all_positions[50:56], 57)
        return FakeResponse(all_positions[start:start + 10], 57)

    source = EightfoldSource(
        "Qualcomm",
        "https://careers.qualcomm.com/careers",
        "qualcomm.com",
        query="intern",
    )

    with patch("requests.Session.get", new=fake_get):
        jobs = source.fetch()

    assert len(jobs) == 57
    assert len({j.external_id for j in jobs}) == 57
    assert "pagination repaired" in source.last_scan_note


def test_incomplete_scan_still_fails_closed():
    # Even repair passes cannot conjure a genuinely omitted record. The source
    # must raise rather than syncing a partial snapshot.
    positions = [pos(i) for i in range(56)]

    def fake_get(self, url, params=None, timeout=None):
        start = int(params["start"])
        return FakeResponse(positions[start:start + 10], 57)

    source = EightfoldSource(
        "Qualcomm",
        "https://careers.qualcomm.com/careers",
        "qualcomm.com",
        query="intern",
    )

    with patch("requests.Session.get", new=fake_get):
        try:
            source.fetch()
        except RuntimeError as exc:
            assert "company was NOT synced" in str(exc)
        else:
            raise AssertionError("partial Eightfold snapshot should not be returned")
