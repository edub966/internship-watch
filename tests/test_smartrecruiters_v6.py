import pytest

import src.main as main
from src.sources.smartrecruiters import SmartRecruitersSource


class Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class SmartRecruitersSession:
    def __init__(self):
        self.headers = {}
        self.list_calls = []
        self.detail_calls = []

    def get(self, url, params=None, timeout=None):
        if params is not None:
            self.list_calls.append((url, params))
            if params["offset"] == 0:
                return Resp({
                    "totalFound": 3,
                    "content": [
                        {
                            "id": "SR-1",
                            "name": "Embedded Systems Intern 2027",
                            "location": {"fullLocation": "Pittsburgh, PA, United States"},
                        },
                        {
                            "id": "SR-2",
                            "name": "Director, Internal Communications",
                            "location": {"fullLocation": "Chicago, IL, United States"},
                        },
                    ],
                })
            return Resp({
                "totalFound": 3,
                "content": [{
                    "id": "SR-3",
                    "name": "Software Co-op 2027",
                    "location": {"fullLocation": "Austin, TX, United States"},
                }],
            })

        self.detail_calls.append(url)
        posting_id = url.rsplit("/", 1)[-1]
        return Resp({
            # The listing posting ID remains the durable external key even if
            # a provider detail payload is inconsistent.
            "id": f"detail-{posting_id}",
            "name": "Embedded Systems Intern 2027" if posting_id == "SR-1" else "Software Co-op 2027",
            "refNumber": "REF-42",
            "releasedDate": "2026-08-24T10:00:00Z",
            "location": {"fullLocation": "Austin, Texas, United States"},
            "typeOfEmployment": {"label": "Intern"},
            "jobAd": {"sections": {
                "jobDescription": {"text": "<p>Firmware, C++, and embedded systems.</p>"},
                "qualifications": {"text": "Pursuing a Bachelor's degree."},
            }},
            "applyUrl": f"https://jobs.smartrecruiters.com/TestCo/{posting_id}",
        })


def test_smartrecruiters_paginates_filters_and_normalizes_details(monkeypatch):
    session = SmartRecruitersSession()
    monkeypatch.setattr("src.sources.smartrecruiters.requests.Session", lambda: session)
    source = SmartRecruitersSource(
        "TestCo", "TestCo", query="intern", country="us", page_size=2, max_pages=2,
    )

    jobs = source.fetch()

    assert [job.external_id for job in jobs] == ["SR-1", "SR-3"]
    assert all(job.source == "smartrecruiters" for job in jobs)
    assert jobs[0].posted_at == "2026-08-24T10:00:00Z"
    assert jobs[0].location == "Austin, Texas, United States"
    assert "embedded systems" in jobs[0].description
    assert "Bachelor's degree" in jobs[0].description
    assert jobs[0].url.endswith("/SR-1")
    assert [call[1]["offset"] for call in session.list_calls] == [0, 2]
    assert all(call[1]["country"] == "us" for call in session.list_calls)
    assert all(call[1]["destination"] == "PUBLIC" for call in session.list_calls)
    assert len(session.detail_calls) == 2
    assert "3 provider rows, 2 internship-title candidates" in source.last_scan_note


def test_smartrecruiters_refuses_a_truncated_snapshot(monkeypatch):
    session = SmartRecruitersSession()
    monkeypatch.setattr("src.sources.smartrecruiters.requests.Session", lambda: session)
    source = SmartRecruitersSource("TestCo", "TestCo", page_size=2, max_pages=1)

    with pytest.raises(RuntimeError, match="company was NOT synced"):
        source.fetch()
    assert session.detail_calls == []


def test_source_factory_supports_smartrecruiters():
    source = main.build_source({
        "name": "Bosch",
        "type": "smartrecruiters",
        "identifier": "BoschGroup",
        "country": "us",
        "title_terms": ["intern", "co-op"],
    })
    assert isinstance(source, SmartRecruitersSource)
    assert source.identifier == "BoschGroup"
    assert source.title_terms == ("intern", "co-op")
