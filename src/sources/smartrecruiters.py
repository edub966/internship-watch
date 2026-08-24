from __future__ import annotations

from typing import Iterable, List

import requests

from src.models import Job
from src.sources.base import JobSource, title_matches_terms


DEFAULT_TITLE_TERMS = ("intern", "internship", "co-op", "coop", "student")


class SmartRecruitersSource(JobSource):
    """Fetch public postings from SmartRecruiters' documented Posting API.

    Some large tenants do not consistently honor the public API's ``q``
    parameter. The adapter therefore also applies a deterministic local title
    filter before requesting posting details. That keeps a board such as Bosch
    to a small number of detail calls while still using the provider's country
    filter and complete offset pagination.
    """

    def __init__(
        self,
        company: str,
        identifier: str,
        query: str = "intern",
        country: str = "us",
        title_terms: Iterable[str] | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ):
        self.company = company
        self.identifier = identifier
        self.query = query
        self.country = country
        self.title_terms = tuple(
            str(term).strip().lower()
            for term in (title_terms or DEFAULT_TITLE_TERMS)
            if str(term).strip()
        )
        self.page_size = min(100, max(1, int(page_size)))
        self.max_pages = max(1, int(max_pages))
        self.base = f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
        self.last_scan_note = ""

    def _matches_title(self, posting: dict) -> bool:
        return not self.title_terms or title_matches_terms(posting.get("name") or "", self.title_terms)

    @staticmethod
    def _location_text(posting: dict) -> str:
        location = posting.get("location") or {}
        if not isinstance(location, dict):
            return str(location or "")
        if location.get("fullLocation"):
            return str(location["fullLocation"])
        values = [location.get("city"), location.get("region"), location.get("country")]
        return ", ".join(str(value).strip() for value in values if value)

    @staticmethod
    def _description(posting: dict) -> str:
        pieces = []
        job_ad = posting.get("jobAd") or {}
        sections = job_ad.get("sections") if isinstance(job_ad, dict) else {}
        if isinstance(sections, dict):
            for section in sections.values():
                if isinstance(section, dict):
                    text = section.get("text")
                else:
                    text = section
                if text:
                    pieces.append(str(text))

        for key in ("typeOfEmployment", "experienceLevel", "department", "function"):
            value = posting.get(key) or {}
            label = value.get("label") if isinstance(value, dict) else value
            if label:
                pieces.append(str(label))
        if posting.get("refNumber"):
            pieces.append(f"Requisition {posting['refNumber']}")
        return " ".join(pieces)

    def _to_job(self, posting: dict, external_id: str | None = None) -> Job:
        posting_id = external_id or str(
            posting.get("id") or posting.get("uuid") or posting.get("refNumber") or ""
        )
        apply_url = posting.get("applyUrl")
        if not apply_url:
            apply_url = f"https://jobs.smartrecruiters.com/{self.identifier}/{posting_id}"
        return Job(
            company=self.company,
            external_id=posting_id,
            title=str(posting.get("name") or ""),
            location=self._location_text(posting),
            url=str(apply_url),
            source="smartrecruiters",
            posted_at=posting.get("releasedDate"),
            description=self._description(posting),
        )

    def fetch(self) -> List[Job]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "InternshipWatch/0.6 (+personal internship tracker)",
            "Accept": "application/json",
        })

        candidates: dict[str, dict] = {}
        total = 0
        offset = 0
        pages = 0

        while pages < self.max_pages:
            params = {
                "limit": self.page_size,
                "offset": offset,
                "q": self.query,
                "destination": "PUBLIC",
            }
            if self.country:
                params["country"] = self.country
            response = session.get(self.base, params=params, timeout=25)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("SmartRecruiters returned a non-object listing payload")
            rows = payload.get("content") or []
            if not isinstance(rows, list):
                raise RuntimeError("SmartRecruiters listing payload has no content list")

            pages += 1
            total = max(total, int(payload.get("totalFound") or 0))
            for posting in rows:
                if not isinstance(posting, dict) or not self._matches_title(posting):
                    continue
                posting_id = str(posting.get("id") or posting.get("uuid") or "")
                if posting_id:
                    candidates[posting_id] = posting

            offset += len(rows)
            if not rows or offset >= total or len(rows) < self.page_size:
                break

        if total and offset < total:
            raise RuntimeError(
                f"SmartRecruiters reports {total} postings but max_pages={self.max_pages} "
                f"only covered {offset}; company was NOT synced"
            )

        jobs: List[Job] = []
        detail_failures = 0
        for posting_id, summary in candidates.items():
            posting = summary
            try:
                response = session.get(f"{self.base}/{posting_id}", timeout=25)
                response.raise_for_status()
                detail = response.json()
                if isinstance(detail, dict):
                    posting = detail
            except (requests.RequestException, ValueError):
                # A valid listing remains useful when its detail page is
                # temporarily unavailable. Eligibility will remain uncertain
                # rather than becoming a fabricated rejection.
                detail_failures += 1
            job = self._to_job(posting, external_id=posting_id)
            if job.external_id:
                jobs.append(job)

        note = (
            f"{total} provider rows, {len(candidates)} internship-title candidates, "
            f"{len(candidates)} detail lookups"
        )
        if detail_failures:
            note += f", WARNING {detail_failures} detail failure(s) kept as uncertain"
        self.last_scan_note = note
        return jobs
