from typing import List
import requests

from src.models import Job
from src.sources.base import JobSource


class LeverSource(JobSource):
    def __init__(self, company: str, site: str, max_pages: int = 10):
        self.company = company
        self.site = site
        self.max_pages = max(1, int(max_pages))
        self.last_scan_note = ""

    def fetch(self) -> List[Job]:
        url = f"https://api.lever.co/v0/postings/{self.site}"
        rows = []
        page_size = 100
        for page in range(self.max_pages):
            r = requests.get(
                url,
                params={"mode": "json", "limit": page_size, "skip": page * page_size},
                timeout=25,
            )
            r.raise_for_status()
            page_rows = r.json()
            if not isinstance(page_rows, list):
                raise RuntimeError("Lever listing payload is not a list")
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
        else:
            raise RuntimeError(
                f"Lever scan reached max_pages={self.max_pages} with a full final page; "
                "company was NOT synced"
            )

        unique_rows = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("id") or row.get("hostedUrl") or "")
            if key:
                unique_rows[key] = row
        rows = list(unique_rows.values())
        self.last_scan_note = f"{len(rows)} provider rows across {page + 1} page(s)"
        out: List[Job] = []
        for p in rows:
            categories = p.get("categories") or {}
            out.append(Job(
                company=self.company,
                external_id=str(p.get("id", p.get("hostedUrl", ""))),
                title=p.get("text", ""),
                location=categories.get("location", ""),
                url=p.get("hostedUrl", ""),
                source="lever",
                description=(p.get("descriptionPlain") or p.get("description") or ""),
            ))
        return out
