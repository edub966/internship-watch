import re
from typing import List

import requests

from src.models import Job
from src.sources.base import JobSource


class AppleSource(JobSource):
    """Fetch Apple Jobs search results from the public jobs.apple.com API.

    Apple uses a CSRF bootstrap token/session for its public search frontend.
    We acquire that token normally, then paginate the USA-only search. No
    authentication, browser automation, or private account access is used.
    """

    ROOT = "https://jobs.apple.com"
    TOKEN_URL = ROOT + "/api/v1/CSRFToken"
    SEARCH_URL = ROOT + "/api/v1/search"

    def __init__(
        self,
        company: str = "Apple",
        query: str = "intern",
        locale: str = "en-us",
        location_filter: str = "postLocation-USA",
        max_pages: int = 25,
        team: str = "",
        sub_team: str = "",
    ):
        self.company = company
        self.query = query
        self.locale = locale
        self.location_filter = location_filter
        self.max_pages = max(1, int(max_pages))
        self.team = str(team or "").strip()
        self.sub_team = str(sub_team or "").strip()
        self.last_scan_note = ""

    @staticmethod
    def _slug(title: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
        return value or "job"

    @staticmethod
    def _extract_rows(payload: dict) -> tuple[list, int]:
        body = payload.get("res") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            body = payload if isinstance(payload, dict) else {}
        rows = body.get("searchResults") or []
        try:
            total = int(body.get("totalRecords") or len(rows))
        except (TypeError, ValueError):
            raise RuntimeError("Apple Jobs returned an invalid totalRecords value")
        if not isinstance(rows, list):
            raise RuntimeError("Apple Jobs returned a non-list searchResults payload")
        return rows, total

    @staticmethod
    def _location(row: dict) -> str:
        names = []
        for loc in row.get("locations") or []:
            if isinstance(loc, dict):
                value = loc.get("name") or loc.get("locationName")
            else:
                value = loc
            if value and str(value).strip():
                names.append(str(value).strip())
        # Search is hard-filtered to USA at the source. Preserve that fact in
        # every row so the downstream gate never infers a country from a city.
        text = " | ".join(dict.fromkeys(names))
        if text:
            return text + " | United States"
        return "United States"

    def _bootstrap(self, session: requests.Session) -> str:
        r = session.get(self.TOKEN_URL, timeout=25)
        r.raise_for_status()
        token = r.headers.get("X-Apple-CSRF-Token") or r.headers.get("x-apple-csrf-token")
        if token:
            return token
        # Some deployments return the token in JSON rather than a header.
        try:
            data = r.json()
        except Exception:
            data = {}
        if isinstance(data, dict):
            token = data.get("csrfToken") or data.get("token")
        if not token:
            raise RuntimeError("Apple Jobs CSRF bootstrap did not return a token")
        return str(token)

    def fetch(self) -> List[Job]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "InternshipWatch/0.5 (+personal internship tracker)",
            "Accept": "application/json",
            "Origin": self.ROOT,
            "Referer": f"{self.ROOT}/{self.locale}/search",
        })
        token = self._bootstrap(session)
        headers = {"X-Apple-CSRF-Token": token, "Content-Type": "application/json"}

        jobs: List[Job] = []
        seen: set[str] = set()
        total = 0

        for page in range(1, self.max_pages + 1):
            filters = {"locations": [self.location_filter]}
            if self.team and self.sub_team:
                filters["teams"] = [{
                    "team": f"teamsAndSubTeams-{self.team.upper()}",
                    "subTeam": f"subTeam-{self.sub_team.upper()}",
                }]
            payload = {
                "query": self.query,
                "filters": filters,
                "page": page,
                "locale": self.locale,
                "sort": "newest",
                "format": {
                    "longDate": "MMMM D, YYYY",
                    "mediumDate": "MMM D, YYYY",
                },
            }
            r = session.post(self.SEARCH_URL, json=payload, headers=headers, timeout=30)
            # Token/session may occasionally rotate. Refresh once rather than
            # retrying blindly or looping.
            if r.status_code in {401, 403}:
                token = self._bootstrap(session)
                headers["X-Apple-CSRF-Token"] = token
                r = session.post(self.SEARCH_URL, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            rows, total = self._extract_rows(r.json())
            if not rows:
                break

            page_new = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ext_id = str(row.get("positionId") or row.get("id") or "").strip()
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                page_new += 1

                title = str(row.get("postingTitle") or row.get("transformedPostingTitle") or row.get("title") or "").strip()
                url = f"{self.ROOT}/{self.locale}/details/{ext_id}/{self._slug(title)}"
                jobs.append(Job(
                    company=self.company,
                    external_id=ext_id,
                    title=title,
                    location=self._location(row),
                    url=url,
                    source="apple",
                    posted_at=row.get("postDateInGMT") or row.get("postingDate"),
                    description=str(row.get("jobSummary") or row.get("description") or ""),
                ))

            if len(seen) >= total:
                break
            if not page_new:
                raise RuntimeError(
                    f"Apple pagination stopped making progress on page {page}; refusing partial scan"
                )
        else:
            if len(seen) < total:
                raise RuntimeError(
                    f"Apple scan hit max_pages={self.max_pages}: {len(seen)} of {total} results collected"
                )

        if total and len(seen) < total:
            raise RuntimeError(
                f"incomplete Apple scan: API reported {total} matches but only {len(seen)} unique jobs were collected"
            )

        scope = "USA source scope"
        if self.team and self.sub_team:
            scope += f", team {self.team}/{self.sub_team}"
        self.last_scan_note = f"{scope}, {len(seen)} unique"
        return jobs
