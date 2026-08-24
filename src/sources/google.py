import ast
import re
from html import unescape
from typing import List
from urllib.parse import parse_qs, urlparse

import requests

from src.models import Job
from src.sources.base import JobSource


class GoogleSource(JobSource):
    """Parse Google’s public jobs page payload.

    Google exposes job data in one or more AF_initDataCallback payloads embedded
    in the page HTML. This adapter intentionally reads the actual job list
    callback without a browser and keeps the source provenance visible to the
    rest of the pipeline.
    """

    def __init__(self, company: str = "Google", url: str | None = None):
        self.company = company
        self.url = url or (
            "https://www.google.com/about/careers/applications/jobs/results/"
            "?location=United%20States&hl=en&jlo=en"
        )
        self.last_scan_note = ""

    @staticmethod
    def _read_payload(html: str):
        for match in re.finditer(r"AF_initDataCallback\((.*?)\)\s*;", html, re.S):
            blob = match.group(1)
            if "jobId" not in blob:
                continue
            try:
                payload = blob.split("data:", 1)[1].split(", sideChannel:", 1)[0].strip()
            except IndexError:
                continue
            normalized = payload.replace("null", "None").replace("true", "True").replace("false", "False")
            try:
                return ast.literal_eval(normalized)
            except Exception:
                continue
        return []

    @staticmethod
    def _parse_jobs_from_payload(data):
        rows = []

        def walk(node):
            if not isinstance(node, list):
                return
            if node and isinstance(node[0], str) and len(node) >= 3:
                candidate_url = str(node[2]) if len(node) > 2 else ""
                if "applications/signin?jobId" in candidate_url or "jobId" in candidate_url:
                    rows.append(node)
                    return
            for child in node:
                walk(child)

        walk(data)
        return rows

    @staticmethod
    def _location_from_url(url: str) -> str:
        if not url:
            return ""
        qs = parse_qs(urlparse(url).query)
        loc = qs.get("loc", [""])[0]
        if loc:
            return loc
        return ""

    @staticmethod
    def _flatten_html_fragments(value):
        out = []
        if isinstance(value, list):
            for item in value:
                out.extend(GoogleSource._flatten_html_fragments(item))
        elif isinstance(value, str):
            out.append(value)
        return out

    @staticmethod
    def _description_from_parts(parts):
        chunks = []
        for part in parts or []:
            for token in GoogleSource._flatten_html_fragments(part):
                if isinstance(token, str):
                    chunks.append(token)
        details = " ".join(chunks)
        details = unescape(details)
        details = re.sub(r"<[^>]+>", " ", details)
        details = re.sub(r"\s+", " ", details).strip()
        return details

    def fetch(self) -> List[Job]:
        r = requests.get(self.url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        payload = self._read_payload(r.text)
        rows = self._parse_jobs_from_payload(payload)
        jobs: List[Job] = []
        seen: set[str] = set()

        for row in rows:
            if len(row) < 3:
                continue
            ext_id = str(row[0]).strip()
            title = str(row[1]).strip()
            signin_url = str(row[2]).strip() if row[2] else ""
            if not ext_id or not title:
                continue
            if ext_id in seen:
                continue
            seen.add(ext_id)

            loc = self._location_from_url(signin_url)
            description = ""
            if len(row) > 3:
                description = self._description_from_parts(row[3:])
            jobs.append(Job(
                company=self.company,
                external_id=ext_id,
                title=title,
                location=loc,
                url=signin_url,
                source="google",
                posted_at=None,
                description=description,
            ))

        self.last_scan_note = f"Google JS payload: {len(jobs)} rows"
        return jobs
