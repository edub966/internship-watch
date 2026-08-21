import yaml

import src.main as main
from src.models import Job
from src.sources.amazon import AmazonSource
from src.sources.apple import AppleSource


class Resp:
    def __init__(self, data=None, status=200, headers=None):
        self._data = data or {}
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class AmazonSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        assert params["country"] == "USA"
        if params["offset"] == 0:
            return Resp({
                "hits": 2,
                "jobs": [
                    {
                        "id_icims": "A1", "title": "Embedded Software Intern",
                        "city": "Austin", "state": "TX", "job_path": "/en/jobs/A1/x",
                        "posted_date": "2026-08-20", "description": "firmware C++ internship",
                    },
                    {
                        "id_icims": "A2", "title": "Marketing Intern",
                        "city": "Seattle", "state": "WA", "job_path": "/en/jobs/A2/y",
                    },
                ],
            })
        return Resp({"hits": 2, "jobs": []})


def test_amazon_source_uses_hard_usa_scope(monkeypatch):
    session = AmazonSession()
    monkeypatch.setattr("src.sources.amazon.requests.Session", lambda: session)
    source = AmazonSource(result_limit=100, page_delay=0)
    jobs = source.fetch()
    assert [j.external_id for j in jobs] == ["A1", "A2"]
    assert all("USA" in j.location for j in jobs)
    assert jobs[0].url.startswith("https://www.amazon.jobs/")


class AppleSession:
    def __init__(self):
        self.headers = {}
        self.get_calls = 0
        self.post_calls = []

    def get(self, url, timeout=None):
        self.get_calls += 1
        return Resp({}, headers={"X-Apple-CSRF-Token": "csrf-123"})

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append((url, json, headers))
        assert json["filters"]["locations"] == ["postLocation-USA"]
        assert headers["X-Apple-CSRF-Token"] == "csrf-123"
        return Resp({"res": {
            "totalRecords": 1,
            "searchResults": [{
                "positionId": "200123456",
                "postingTitle": "Hardware Engineering Intern",
                "locations": [{"name": "Cupertino"}],
                "postDateInGMT": "2026-08-20",
                "jobSummary": "ASIC verification hardware internship",
            }],
        }})


def test_apple_source_bootstraps_token_and_marks_usa(monkeypatch):
    session = AppleSession()
    monkeypatch.setattr("src.sources.apple.requests.Session", lambda: session)
    jobs = AppleSource(max_pages=2).fetch()
    assert len(jobs) == 1
    assert jobs[0].external_id == "200123456"
    assert "United States" in jobs[0].location
    assert jobs[0].url.startswith("https://jobs.apple.com/en-us/details/200123456/")
    assert session.get_calls == 1
    assert len(session.post_calls) == 1


def test_build_source_supports_new_adapters():
    a = main.build_source({"name": "Amazon", "type": "amazon", "query": "intern", "country": "USA"})
    b = main.build_source({"name": "Apple", "type": "apple", "query": "intern"})
    assert isinstance(a, AmazonSource)
    assert isinstance(b, AppleSource)


def test_roster_check_has_no_db_or_tavily_side_effects(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "companies.yaml"
    cfg.write_text(yaml.safe_dump({
        "companies": [{
            "name": "TestChip", "enabled": True, "type": "workday",
            "host": "x", "tenant": "x", "site": "x", "target_year": 2027, "us_only": True,
        }],
        "staged_companies": [{"name": "LaterCo", "reason": "not commissioned"}],
    }))

    class Source:
        last_scan_note = "mocked"
        def fetch(self):
            return [Job(
                company="TestChip", external_id="R1", title="ASIC Design Intern 2027",
                location="Austin, TX", url="https://example.com", source="workday",
                description="ASIC RTL verification internship",
            )]

    monkeypatch.setattr(main, "build_source", lambda c: Source())
    monkeypatch.setattr(main, "JobDB", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no DB")))
    monkeypatch.setattr(main, "TavilyBudget", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Tavily")))
    main.roster_check(str(cfg), 12.0)
    out = capsys.readouterr().out
    assert "ROSTER CHECK" in out
    assert "OK   TestChip" in out
    assert "1 US-eligible" in out
    assert "LaterCo" in out
