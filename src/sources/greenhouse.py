from __future__ import annotations

from typing import Iterable, List

import requests

from src.models import Job
from src.sources.base import JobSource, title_matches_terms


class GreenhouseSource(JobSource):
    """Fetch a Greenhouse board, with an efficient large-board mode.

    When ``title_terms`` is configured, the source first downloads the compact
    listing without descriptions and then resolves details only for matching
    internship/student titles. Existing callers without title terms retain the
    provider's one-request ``content=true`` behavior.
    """

    def __init__(
        self,
        company: str,
        board_token: str,
        title_terms: Iterable[str] | None = None,
        max_detail_resolutions: int = 100,
    ):
        self.company = company
        self.board_token = board_token
        self.title_terms = tuple(
            str(term).strip().lower()
            for term in (title_terms or [])
            if str(term).strip()
        )
        self.max_detail_resolutions = max(1, int(max_detail_resolutions))
        self.last_scan_note = ""

    def _to_job(self, posting: dict) -> Job:
        location = (posting.get("location") or {}).get("name", "")
        return Job(
            company=self.company,
            external_id=str(posting.get("id", posting.get("absolute_url", ""))),
            title=posting.get("title", ""),
            location=location,
            url=posting.get("absolute_url", ""),
            source="greenhouse",
            posted_at=posting.get("updated_at"),
            description=posting.get("content", "") or "",
        )

    def fetch(self) -> List[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs"
        session = requests.Session()
        session.headers.update({
            "User-Agent": "InternshipWatch/0.6 (+personal internship tracker)",
            "Accept": "application/json",
        })
        r = session.get(
            url,
            params={"content": "false" if self.title_terms else "true"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("jobs", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            raise RuntimeError("Greenhouse listing payload has no jobs list")

        if not self.title_terms:
            self.last_scan_note = f"{len(rows)} provider rows"
            return [self._to_job(posting) for posting in rows if isinstance(posting, dict)]

        candidates = [
            posting
            for posting in rows
            if isinstance(posting, dict)
            and title_matches_terms(posting.get("title") or "", self.title_terms)
        ]
        if len(candidates) > self.max_detail_resolutions:
            raise RuntimeError(
                f"Greenhouse found {len(candidates)} internship-title candidates, exceeding "
                f"max_detail_resolutions={self.max_detail_resolutions}; company was NOT synced"
            )

        out: List[Job] = []
        detail_failures = 0
        for summary in candidates:
            posting = summary
            posting_id = summary.get("id")
            if posting_id:
                try:
                    detail_response = session.get(f"{url}/{posting_id}", timeout=25)
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    if isinstance(detail, dict):
                        posting = detail
                except (requests.RequestException, ValueError):
                    detail_failures += 1
            out.append(self._to_job(posting))

        note = (
            f"{len(rows)} provider rows, {len(candidates)} internship-title candidates, "
            f"{len(candidates)} detail lookups"
        )
        if detail_failures:
            note += f", WARNING {detail_failures} detail failure(s) kept as uncertain"
        self.last_scan_note = note
        return out
