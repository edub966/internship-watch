import time
from typing import List
from urllib.parse import urljoin

import requests

from src.models import Job
from src.sources.base import JobSource


class AmazonSource(JobSource):
    """Fetch Amazon Jobs search results from its public JSON search endpoint.

    The source is deliberately scoped to USA results before they enter the
    pipeline. We still run the normal location guard afterwards; the explicit
    USA marker added here makes the provenance unambiguous.
    """

    SEARCH_URL = "https://www.amazon.jobs/en/search.json"
    SITE_ROOT = "https://www.amazon.jobs"

    def __init__(
        self,
        company: str = "Amazon",
        query: str = "intern",
        country: str = "USA",
        result_limit: int = 100,
        max_pages: int = 20,
        page_delay: float = 0.10,
    ):
        self.company = company
        self.query = query
        self.country = country
        self.result_limit = max(1, min(int(result_limit), 100))
        self.max_pages = max(1, int(max_pages))
        self.page_delay = max(0.0, float(page_delay))
        self.last_scan_note = ""

    @staticmethod
    def _location(row: dict, country: str) -> str:
        pieces = []
        for key in ("city", "state", "country_code"):
            value = row.get(key)
            if value and str(value).strip():
                pieces.append(str(value).strip())
        # Amazon's country=USA query is the hard source-level scope. Preserve an
        # explicit country marker so downstream classification can fail safely
        # without relying on city-name heuristics.
        if country.upper() == "USA" and not any("USA" in p.upper() or "US" == p.upper() for p in pieces):
            pieces.append("USA")
        return ", ".join(dict.fromkeys(pieces))

    def fetch(self) -> List[Job]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "InternshipWatch/0.5 (+personal internship tracker)",
            "Accept": "application/json",
        })

        jobs: List[Job] = []
        seen: set[str] = set()
        offset = 0
        reported_total = None

        for page in range(self.max_pages):
            params = {
                "base_query": self.query,
                "country": self.country,
                "offset": offset,
                "result_limit": self.result_limit,
            }
            r = session.get(self.SEARCH_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            rows = data.get("jobs") or []
            if not isinstance(rows, list):
                raise RuntimeError("Amazon Jobs returned a non-list jobs payload")

            try:
                reported_total = int(data.get("hits") or 0)
            except (TypeError, ValueError):
                raise RuntimeError("Amazon Jobs returned an invalid hits count")

            if not rows:
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                ext_id = str(row.get("id_icims") or row.get("id") or row.get("job_path") or "").strip()
                if not ext_id:
                    continue
                if ext_id in seen:
                    continue
                seen.add(ext_id)

                path = str(row.get("job_path") or "").strip()
                url = urljoin(self.SITE_ROOT, path) if path else ""
                desc_parts = [
                    row.get("description") or "",
                    row.get("basic_qualifications") or "",
                    row.get("preferred_qualifications") or "",
                ]
                jobs.append(Job(
                    company=self.company,
                    external_id=ext_id,
                    title=str(row.get("title") or "").strip(),
                    location=self._location(row, self.country),
                    url=url,
                    source="amazon",
                    posted_at=row.get("posted_date"),
                    description=" ".join(str(x) for x in desc_parts if x),
                ))

            offset += len(rows)
            if reported_total <= offset:
                break
            if self.page_delay:
                time.sleep(self.page_delay)
        else:
            raise RuntimeError(
                f"Amazon scan hit max_pages={self.max_pages} before exhausting {reported_total} matches"
            )

        if reported_total is not None and len(seen) < min(reported_total, offset):
            # Duplicate result pages are unexpected here. Refuse to sync a
            # partial snapshot rather than creating false disappearance/new-job
            # churn later.
            raise RuntimeError(
                f"incomplete Amazon scan: API reported {reported_total} matches but only "
                f"{len(seen)} unique jobs were collected"
            )

        self.last_scan_note = f"USA source scope, {len(seen)} unique"
        return jobs
