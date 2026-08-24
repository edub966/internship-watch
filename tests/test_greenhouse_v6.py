from src.sources.greenhouse import GreenhouseSource


class Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class GreenhouseSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if params is not None:
            return Resp({"jobs": [
                {
                    "id": 101,
                    "title": "Systems Software Intern 2027",
                    "location": {"name": "Austin, TX"},
                    "absolute_url": "https://job-boards.greenhouse.io/test/jobs/101",
                    "updated_at": "2026-08-24T12:00:00Z",
                },
                {
                    "id": 102,
                    "title": "Vice President, Internal Audit",
                    "location": {"name": "New York, NY"},
                    "absolute_url": "https://job-boards.greenhouse.io/test/jobs/102",
                },
            ]})
        return Resp({
            "id": 101,
            "title": "Systems Software Intern 2027",
            "location": {"name": "Austin, TX"},
            "absolute_url": "https://job-boards.greenhouse.io/test/jobs/101",
            "updated_at": "2026-08-24T12:00:00Z",
            "content": "<p>Linux, C++, networking, and distributed systems.</p>",
        })


def test_greenhouse_large_board_mode_resolves_only_internship_titles(monkeypatch):
    session = GreenhouseSession()
    monkeypatch.setattr("src.sources.greenhouse.requests.Session", lambda: session)
    source = GreenhouseSource("TestCo", "test", title_terms=["intern", "co-op"])

    jobs = source.fetch()

    assert len(jobs) == 1
    assert jobs[0].external_id == "101"
    assert jobs[0].description.startswith("<p>Linux")
    assert session.calls[0][1] == {"content": "false"}
    assert session.calls[1][0].endswith("/jobs/101")
    assert len(session.calls) == 2
    assert "2 provider rows, 1 internship-title candidates" in source.last_scan_note


def test_greenhouse_legacy_mode_keeps_single_content_request(monkeypatch):
    session = GreenhouseSession()
    monkeypatch.setattr("src.sources.greenhouse.requests.Session", lambda: session)
    jobs = GreenhouseSource("TestCo", "test").fetch()
    assert len(jobs) == 2
    assert session.calls == [
        ("https://boards-api.greenhouse.io/v1/boards/test/jobs", {"content": "true"})
    ]
