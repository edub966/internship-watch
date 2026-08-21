from typing import List
import hashlib
import json
import requests
from bs4 import BeautifulSoup

from src.models import Job
from src.sources.base import JobSource


class GenericJsonLdSource(JobSource):
    """Fallback for pages that expose schema.org JobPosting JSON-LD.

    This intentionally does not run a headless browser or bypass bot controls.
    """

    def __init__(self, company: str, url: str):
        self.company = company
        self.url = url

    def fetch(self) -> List[Job]:
        r = requests.get(self.url, timeout=25, headers={"User-Agent": "InternshipWatch/0.1"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out: List[Job] = []

        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = tag.string or tag.get_text() or ""
            try:
                parsed = json.loads(raw)
            except Exception:
                continue

            items = parsed if isinstance(parsed, list) else [parsed]
            flattened = []
            for item in items:
                if isinstance(item, dict) and "@graph" in item:
                    flattened.extend(item.get("@graph") or [])
                else:
                    flattened.append(item)

            for item in flattened:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                title = item.get("title", "")
                url = item.get("url", self.url)
                identifier = item.get("identifier")
                if isinstance(identifier, dict):
                    identifier = identifier.get("value")
                ext = identifier or hashlib.sha256(f"{self.company}|{title}|{url}".encode()).hexdigest()[:20]

                loc = ""
                job_loc = item.get("jobLocation")
                if isinstance(job_loc, list) and job_loc:
                    job_loc = job_loc[0]
                if isinstance(job_loc, dict):
                    addr = job_loc.get("address") or {}
                    if isinstance(addr, dict):
                        loc = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")] if x)

                out.append(Job(
                    company=self.company,
                    external_id=str(ext),
                    title=title,
                    location=loc,
                    url=url,
                    source="generic-jsonld",
                    posted_at=item.get("datePosted"),
                    description=item.get("description", "") or "",
                ))
        return out
