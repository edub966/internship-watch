from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import requests

from src.models import Job
from src.sources.base import JobSource


class EightfoldSource(JobSource):
    """Fetch public job listings from Eightfold PCS-X career sites.

    Eightfold fixes search pages at 10 rows. In practice, timestamp-sorted
    pagination can occasionally repeat a row across a page boundary. A naive
    0,10,20... scan then misses a different posting even though the API's
    reported count is unchanged.

    This source therefore:
      1. performs the normal timestamp-ordered pass,
      2. repairs duplicate page boundaries with nearby offsets,
      3. if still short, performs shifted timestamp passes,
      4. refuses to return an incomplete snapshot.

    The last rule is important for a monitoring bot: an incomplete scan must
    never be interpreted as jobs closing or later becoming "new".
    """

    PAGE_SIZE = 10

    def __init__(
        self,
        company: str,
        board_url: str,
        domain: str,
        query: str = "intern",
        max_pages: int = 20,
    ):
        self.company = company
        self.board_url = board_url.rstrip("/")
        parsed = urlparse(self.board_url)
        self.root = f"{parsed.scheme}://{parsed.netloc}"
        self.domain = domain
        self.query = query
        self.max_pages = max_pages
        self.last_scan_note = ""

    @staticmethod
    def _location_text(position: dict) -> str:
        values = position.get("standardizedLocations") or position.get("locations") or []
        if isinstance(values, str):
            return values
        if isinstance(values, list):
            return " | ".join(str(v) for v in values if v)
        return ""

    @staticmethod
    def _posted_at(position: dict):
        value = position.get("postedTs") or position.get("creationTs")
        if value is None:
            return None
        try:
            value = float(value)
            if value > 10_000_000_000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return str(value)

    @staticmethod
    def _search_metadata(position: dict) -> str:
        pieces = []
        for key in (
            "department",
            "businessUnit",
            "jobFamily",
            "jobFunction",
            "workLocationOption",
        ):
            value = position.get(key)
            if value:
                pieces.append(str(value))

        for key in ("skills", "standardizedSkills", "skillNames"):
            value = position.get(key)
            if isinstance(value, list):
                pieces.extend(str(v) for v in value if v)
            elif value:
                pieces.append(str(value))
        return " ".join(pieces)

    def _position_to_job(self, p: dict, pid: str) -> Job:
        path = p.get("publicUrl") or p.get("positionUrl") or f"/careers/job/{pid}"
        url = path if str(path).startswith("http") else urljoin(self.root, str(path))
        return Job(
            company=self.company,
            external_id=str(p.get("displayJobId") or p.get("atsJobId") or pid),
            title=str(p.get("name") or p.get("title") or ""),
            location=self._location_text(p),
            url=url,
            source="eightfold",
            posted_at=self._posted_at(p),
            description=self._search_metadata(p),
        )

    def _request_page(self, session: requests.Session, start: int) -> Tuple[List[dict], int]:
        params = {
            "domain": self.domain,
            "query": self.query,
            "location": "",
            "start": str(max(0, start)),
            "sort_by": "timestamp",
            "filter_include_remote": "1",
        }
        r = session.get(f"{self.root}/api/pcsx/search", params=params, timeout=25)
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") or {}
        positions = [p for p in (data.get("positions") or []) if isinstance(p, dict)]
        total = int(data.get("count") or 0)
        return positions, total

    def _merge_page(
        self,
        positions: List[dict],
        jobs_by_pid: Dict[str, Job],
    ) -> int:
        """Merge one page into the snapshot and return duplicate-row count."""
        duplicates = 0
        for p in positions:
            pid = str(p.get("id") or p.get("displayJobId") or p.get("atsJobId") or "")
            if not pid:
                continue
            if pid in jobs_by_pid:
                duplicates += 1
                continue
            jobs_by_pid[pid] = self._position_to_job(p, pid)
        return duplicates

    def fetch(self) -> List[Job]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; InternshipWatch/0.4)",
            "Accept": "application/json",
            "Referer": self.board_url,
        })

        jobs_by_pid: Dict[str, Job] = {}
        total = 0
        duplicate_boundaries = set()
        requests_made = 0

        # Pass 1: normal 0,10,20... timestamp pagination.
        for page in range(self.max_pages):
            start = page * self.PAGE_SIZE
            positions, page_total = self._request_page(session, start)
            requests_made += 1
            if page_total:
                total = max(total, page_total)

            if not positions:
                break

            if self._merge_page(positions, jobs_by_pid):
                duplicate_boundaries.add(start)

            if len(positions) < self.PAGE_SIZE:
                break
            if total and start + len(positions) >= total:
                break

        if total > self.max_pages * self.PAGE_SIZE:
            raise RuntimeError(
                f"Eightfold reports {total} matches, exceeding configured scan capacity "
                f"of {self.max_pages * self.PAGE_SIZE}; increase max_pages"
            )

        initial_unique = len(jobs_by_pid)

        # Pass 2: if a normal page repeated a row, probe immediately on either
        # side of that boundary. This usually repairs the exact Qualcomm-style
        # failure with only one or two extra requests.
        if total and len(jobs_by_pid) < total and duplicate_boundaries:
            max_start = max(0, total - 1)
            repair_starts = set()
            for start in duplicate_boundaries:
                repair_starts.add(max(0, start - 1))
                repair_starts.add(min(max_start, start + 1))

            for start in sorted(repair_starts):
                positions, page_total = self._request_page(session, start)
                requests_made += 1
                if page_total:
                    total = max(total, page_total)
                self._merge_page(positions, jobs_by_pid)
                if total and len(jobs_by_pid) >= total:
                    break

        # Pass 3: if the tie-ordering is unstable enough that local repair did
        # not fill the snapshot, exhaust every shifted page boundary. Unioning
        # all ten boundary alignments avoids depending on one particular tie
        # order while remaining bounded by the provider-reported result count.
        if total and len(jobs_by_pid) < total:
            for offset in range(1, self.PAGE_SIZE):
                for start in range(offset, total, self.PAGE_SIZE):
                    positions, page_total = self._request_page(session, start)
                    requests_made += 1
                    if page_total:
                        total = max(total, page_total)
                    if not positions:
                        continue
                    self._merge_page(positions, jobs_by_pid)
                    if total and len(jobs_by_pid) >= total:
                        break
                if total and len(jobs_by_pid) >= total:
                    break

        # Never sync a partial snapshot. This is what prevents false closures
        # and false "new job" alerts on a later rediscovery.
        if total and len(jobs_by_pid) < total:
            raise RuntimeError(
                f"incomplete Eightfold scan after pagination repair: API reported "
                f"{total} matches but only {len(jobs_by_pid)} unique jobs were "
                f"collected across {requests_made} requests; company was NOT synced"
            )

        repaired = len(jobs_by_pid) - initial_unique
        if repaired > 0:
            self.last_scan_note = f"pagination repaired +{repaired} job(s)"
        else:
            self.last_scan_note = ""

        return list(jobs_by_pid.values())
