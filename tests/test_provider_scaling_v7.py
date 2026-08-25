import pytest

from src.sources.lever import LeverSource
from src.sources.workday import WorkdaySource


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FacetedWorkdaySession:
    def __init__(self, matching_value=True, include_facet=True):
        self.headers = {}
        self.payloads = []
        self.matching_value = matching_value
        self.include_facet = include_facet

    def post(self, url, json=None, timeout=None):
        self.payloads.append(json)
        if len(self.payloads) == 1:
            values = [{"id": "regular", "descriptor": "Regular"}]
            if self.matching_value:
                values.append({"id": "intern", "descriptor": "Intern/Co-op (Fixed Term)"})
            facets = [{"facetParameter": "workerSubType", "values": values}] if self.include_facet else []
            return Response({"total": 1500, "jobPostings": [], "facets": facets})

        assert json["appliedFacets"] == {"workerSubType": ["intern"]}
        return Response({
            "total": 1,
            "jobPostings": [{
                "title": "ASIC Design Intern 2027",
                "externalPath": "/job/Austin/ASIC-Design-Intern_R1",
                "locationsText": "Austin, TX",
                "postedOn": "Posted Today",
                "bulletFields": ["R1", "Bachelor's student internship"],
            }],
        })


def test_workday_resolves_provider_facets_before_pagination(monkeypatch):
    session = FacetedWorkdaySession()
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    source = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        target_year=2027,
        facet_terms={"workerSubType": ["intern", "co-op", "student"]},
    )

    jobs = source.fetch()
    assert len(jobs) == 1
    assert len(session.payloads) == 2
    assert session.payloads[0]["limit"] == 1
    assert "provider facet filter workerSubType=1 value" in source.last_scan_note


def test_workday_empty_matching_facet_is_a_healthy_zero_result(monkeypatch):
    session = FacetedWorkdaySession(matching_value=False)
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    source = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        facet_terms={"workerSubType": ["intern"]},
    )
    assert source.fetch() == []
    assert len(session.payloads) == 1
    assert "provider facets healthy" in source.last_scan_note


def test_workday_missing_configured_facet_fails_closed(monkeypatch):
    session = FacetedWorkdaySession(include_facet=False)
    monkeypatch.setattr("src.sources.workday.requests.Session", lambda: session)
    source = WorkdaySource(
        "ChipCo", "chip.wd1.myworkdayjobs.com", "chip", "External",
        facet_terms={"workerSubType": ["intern"]},
    )
    with pytest.raises(RuntimeError, match="facet configuration"):
        source.fetch()


def _lever_row(index):
    return {
        "id": f"job-{index}",
        "text": f"Software Engineering Intern {index}",
        "hostedUrl": f"https://jobs.lever.co/test/job-{index}",
        "categories": {"location": "New York, NY"},
        "descriptionPlain": "Software systems internship",
    }


def test_lever_paginates_instead_of_silently_stopping_at_100(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        skip = params["skip"]
        return Response([_lever_row(i) for i in range(skip, min(skip + 100, 101))])

    monkeypatch.setattr("src.sources.lever.requests.get", fake_get)
    source = LeverSource("Test", "test", max_pages=3)
    jobs = source.fetch()
    assert len(jobs) == 101
    assert [call["skip"] for call in calls] == [0, 100]
    assert "101 provider rows across 2 page(s)" == source.last_scan_note


def test_lever_full_final_page_fails_closed_at_scan_cap(monkeypatch):
    monkeypatch.setattr(
        "src.sources.lever.requests.get",
        lambda *args, **kwargs: Response([_lever_row(i) for i in range(100)]),
    )
    with pytest.raises(RuntimeError, match="NOT synced"):
        LeverSource("Test", "test", max_pages=1).fetch()
