from typing import List
import requests

from src.models import Job
from src.sources.base import JobSource


class GreenhouseSource(JobSource):
    def __init__(self, company: str, board_token: str):
        self.company = company
        self.board_token = board_token

    def fetch(self) -> List[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs"
        r = requests.get(url, params={"content": "true"}, timeout=25)
        r.raise_for_status()
        data = r.json()
        out: List[Job] = []
        for p in data.get("jobs", []):
            location = (p.get("location") or {}).get("name", "")
            out.append(Job(
                company=self.company,
                external_id=str(p.get("id", p.get("absolute_url", ""))),
                title=p.get("title", ""),
                location=location,
                url=p.get("absolute_url", ""),
                source="greenhouse",
                posted_at=p.get("updated_at"),
                description=p.get("content", "") or "",
            ))
        return out
