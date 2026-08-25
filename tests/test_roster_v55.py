from src.sources.apple import AppleSource
from src.sources.workday import WorkdaySource


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


class AppleTeamSession:
    def __init__(self):
        self.headers = {}
        self.payloads = []

    def get(self, url, timeout=None):
        return Resp({}, headers={"X-Apple-CSRF-Token": "csrf"})

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return Resp({"res": {"totalRecords": 1, "searchResults": [{
            "positionId": "2001", "postingTitle": "Hardware Engineering Intern",
            "locations": [{"name": "Cupertino"}], "jobSummary": "ASIC internship",
        }]}})


def test_apple_uses_students_internships_team_filter(monkeypatch):
    session = AppleTeamSession()
    monkeypatch.setattr("src.sources.apple.requests.Session", lambda: session)
    jobs = AppleSource(team="STDNT", sub_team="INTRN", max_pages=2).fetch()
    assert len(jobs) == 1
    assert session.payloads[0]["filters"]["locations"] == ["postLocation-USA"]
    assert session.payloads[0]["filters"]["teams"] == [{"team": "teamsAndSubTeams-STDNT", "subTeam": "subTeam-INTRN"}]
    assert session.payloads[0]["sort"] == "newest"


class WorkdayDetailSession:
    def __init__(self):
        self.headers = {}
        self.posts = 0
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        return Resp({
            "total": 2,
            "jobPostings": [
                {
                    "title": "ASIC Design Intern 2027",
                    "externalPath": "/job/SAN-JOSE/ASIC-Design-Intern_R1",
                    "locationsText": "SAN JOSE",
                    "postedOn": "Posted Today",
                    "bulletFields": ["R1"],
                },
                {
                    "title": "Marketing Intern 2027",
                    "externalPath": "/job/SAN-JOSE/Marketing-Intern_R2",
                    "locationsText": "SAN JOSE",
                    "postedOn": "Posted Today",
                    "bulletFields": ["R2"],
                },
            ],
        })

    def get(self, url, timeout=None):
        self.gets.append(url)
        assert url.endswith("/job/SAN-JOSE/ASIC-Design-Intern_R1")
        return Resp({"jobPostingInfo": {
            "title": "ASIC Design Intern 2027",
            "location": "San Jose, California, United States of America",
            "additionalLocations": ["Austin, Texas, United States of America"],
            "jobDescription": "ASIC RTL verification internship",
            "startDate": "2026-08-21",
        }})


def test_workday_resolves_only_ambiguous_relevant_rows(monkeypatch):
    session = WorkdayDetailSession()
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    jobs = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        target_year=2027,
    ).fetch()
    assert len(jobs) == 2
    assert len(session.gets) == 1
    assert "United States of America" in jobs[0].location
    assert "Austin" in jobs[0].location
    assert jobs[1].location == "SAN JOSE"


class WorkdayEligibilityDetailSession:
    def __init__(self):
        self.headers = {}
        self.gets = []

    def post(self, url, json=None, timeout=None):
        return Resp({
            "total": 1,
            "jobPostings": [{
                "title": "ASIC Design Intern",
                "externalPath": "/job/AUSTIN/ASIC-Design-Intern_R3",
                "locationsText": "Austin, TX",
                "postedOn": "Posted Today",
                "bulletFields": ["R3"],
            }],
        })

    def get(self, url, timeout=None):
        self.gets.append(url)
        return Resp({"jobPostingInfo": {
            "title": "ASIC Design Intern",
            "location": "Austin, Texas, United States of America",
            "jobDescription": "Summer 2027 internship for Bachelor's students doing RTL verification.",
        }})


def test_workday_resolves_missing_summer_and_degree_evidence_from_detail(monkeypatch):
    session = WorkdayEligibilityDetailSession()
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    source = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        target_year=2027,
    )
    jobs = source.fetch()
    assert len(session.gets) == 1
    assert "Summer 2027" in jobs[0].description
    assert "missing cycle/degree record resolved from detail" in source.last_scan_note
