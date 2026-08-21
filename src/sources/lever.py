from typing import List
import requests

from src.models import Job
from src.sources.base import JobSource


class LeverSource(JobSource):
    def __init__(self, company: str, site: str):
        self.company = company
        self.site = site

    def fetch(self) -> List[Job]:
        url = f"https://api.lever.co/v0/postings/{self.site}"
        r = requests.get(url, params={"mode": "json", "limit": 100}, timeout=25)
        r.raise_for_status()
        rows = r.json()
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
