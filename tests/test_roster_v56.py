import yaml

from src.sources.workday import WorkdaySource


class Resp:
    def __init__(self, data=None, status=200):
        self._data = data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class StillAmbiguousSession:
    def __init__(self):
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        return Resp({
            "total": 1,
            "jobPostings": [{
                "title": "ASIC Design Intern 2027",
                "externalPath": "/job/SOMEWHERE/ASIC-Design-Intern_R1",
                "locationsText": "2 Locations",
                "postedOn": "Posted Today",
                "bulletFields": ["R1"],
            }],
        })

    def get(self, url, timeout=None):
        # The detail endpoint answered, but still did not provide enough country
        # context. This must not be reported as a successful classification.
        return Resp({"jobPostingInfo": {
            "title": "ASIC Design Intern 2027",
            "location": "San Jose",
            "additionalLocations": ["Austin"],
            "jobDescription": "ASIC RTL internship",
        }})


def test_workday_note_distinguishes_detail_lookup_from_location_classification(monkeypatch):
    session = StillAmbiguousSession()
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    source = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        target_year=2027,
    )
    jobs = source.fetch()
    assert len(jobs) == 1
    assert "1 Workday detail lookup" in source.last_scan_note
    assert "1 relevant Workday location still ambiguous after detail" in source.last_scan_note
    assert "classified from detail" not in source.last_scan_note


def test_micron_uses_current_eightfold_careers_board():
    with open("config/companies.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    micron = next(c for c in cfg["companies"] if c["name"] == "Micron")
    assert micron["type"] == "eightfold"
    assert micron["board_url"] == "https://careers.micron.com/careers"
    assert micron["domain"] == "micron.com"
    assert micron["query"] == "intern"
