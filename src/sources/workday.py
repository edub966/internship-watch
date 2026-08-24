from typing import Iterable, List, Mapping
import requests

from src.filtering import is_relevant
from src.locations import AMBIGUOUS, classify_us_location
from src.models import Job
from src.sources.base import JobSource


class WorkdaySource(JobSource):
    """Fetch public Workday CXS job postings.

    Workday's list endpoint often returns a vague location (for example a city
    with no state/country, or ``2 Locations``). For postings that already look
    like relevant internships, we resolve only those ambiguous rows through the
    public per-job detail endpoint. This avoids a detail request for every job
    on very large boards while preventing good US internships from failing the
    downstream location gate just because the list view is lossy.
    """

    def __init__(
        self,
        company: str,
        host: str,
        tenant: str,
        site: str,
        search_text: str = "intern",
        target_year: int | None = None,
        resolve_ambiguous_relevant: bool = True,
        max_detail_resolutions: int = 100,
        facet_terms: Mapping[str, Iterable[str]] | None = None,
    ):
        self.company = company
        self.host = host
        self.tenant = tenant
        self.site = site
        self.search_text = search_text
        self.target_year = target_year
        self.resolve_ambiguous_relevant = bool(resolve_ambiguous_relevant)
        self.max_detail_resolutions = max(0, int(max_detail_resolutions))
        self.facet_terms = {
            str(parameter): tuple(
                str(term).strip().lower()
                for term in terms
                if str(term).strip()
            )
            for parameter, terms in (facet_terms or {}).items()
            if str(parameter).strip()
        }
        self.base = f"https://{host}/wday/cxs/{tenant}/{site}"
        self.last_scan_note = ""

    def _resolve_facet_filters(self, data: dict) -> dict[str, list[str]]:
        """Resolve stable facet names to provider-owned IDs on every scan."""
        facets = {
            str(facet.get("facetParameter")): facet
            for facet in (data.get("facets") or [])
            if isinstance(facet, dict) and facet.get("facetParameter")
        }
        missing = sorted(set(self.facet_terms) - set(facets))
        if missing:
            raise RuntimeError(
                "Workday facet configuration no longer matches provider metadata: "
                + ", ".join(missing)
            )

        resolved = {}
        for parameter, terms in self.facet_terms.items():
            values = facets[parameter].get("values") or []
            resolved[parameter] = [
                str(value.get("id"))
                for value in values
                if isinstance(value, dict)
                and value.get("id")
                and any(term in str(value.get("descriptor") or "").lower() for term in terms)
            ]
        return resolved

    @staticmethod
    def _join_locations(primary, additional) -> str:
        values = []
        for value in [primary, *(additional or [])]:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
        return " | ".join(values)

    def _resolve_detail(self, session: requests.Session, job: Job, path: str) -> bool:
        if not path:
            return False
        r = session.get(f"{self.base}{path}", timeout=25)
        r.raise_for_status()
        payload = r.json()
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return False

        richer_location = self._join_locations(info.get("location"), info.get("additionalLocations"))
        if richer_location:
            job.location = richer_location
        if info.get("title"):
            job.title = str(info.get("title"))
        if info.get("jobDescription"):
            job.description = str(info.get("jobDescription"))
        job.posted_at = info.get("startDate") or info.get("postedOn") or job.posted_at
        return True

    def fetch(self) -> List[Job]:
        jobs: List[Job] = []
        paths: dict[str, str] = {}
        offset = 0
        limit = 20
        session = requests.Session()
        session.headers.update({
            "User-Agent": "InternshipWatch/0.5 (+personal internship tracker)",
            "Accept": "application/json",
        })

        applied_facets = {}
        facet_note = ""
        if self.facet_terms:
            probe = session.post(
                f"{self.base}/jobs",
                json={
                    "appliedFacets": {},
                    "limit": 1,
                    "offset": 0,
                    "searchText": self.search_text,
                },
                timeout=25,
            )
            probe.raise_for_status()
            probe_data = probe.json()
            applied_facets = self._resolve_facet_filters(probe_data)
            empty_parameters = [name for name, ids in applied_facets.items() if not ids]
            if empty_parameters:
                self.last_scan_note = (
                    "provider facets healthy; no current values match "
                    + ", ".join(empty_parameters)
                )
                return []
            facet_note = "provider facet filter " + ", ".join(
                f"{name}={len(ids)} value{'s' if len(ids) != 1 else ''}"
                for name, ids in applied_facets.items()
            )

        while True:
            payload = {
                "appliedFacets": applied_facets,
                "limit": limit,
                "offset": offset,
                "searchText": self.search_text,
            }
            r = session.post(f"{self.base}/jobs", json=payload, timeout=25)
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobPostings", [])

            for p in postings:
                path = p.get("externalPath") or ""
                url = f"https://{self.host}/en-US/{self.site}{path}" if path.startswith("/") else path
                ext_id = p.get("bulletFields", [""])[0] if p.get("bulletFields") else path or p.get("title", "")
                job = Job(
                    company=self.company,
                    external_id=str(ext_id),
                    title=p.get("title", ""),
                    location=p.get("locationsText", ""),
                    url=url,
                    source="workday",
                    posted_at=p.get("postedOn"),
                    description=" ".join(p.get("bulletFields", []) or []),
                )
                jobs.append(job)
                paths[job.external_id] = path

            total = int(data.get("total", len(postings)))
            offset += len(postings)
            if not postings or offset >= total:
                break

        detail_lookups = 0
        classified_from_detail = 0
        still_ambiguous = 0
        capped_ambiguous = 0
        if self.resolve_ambiguous_relevant and self.max_detail_resolutions:
            for job in jobs:
                # Only pay the extra HTTP round-trip for a posting that already
                # passes the CE/target-cycle gate and whose list-view location
                # would otherwise fail closed.
                if not is_relevant(job, target_year=self.target_year):
                    continue
                decision = classify_us_location(job.location or "", "workday")
                if decision.status != AMBIGUOUS:
                    continue
                if detail_lookups >= self.max_detail_resolutions:
                    capped_ambiguous += 1
                    continue
                detail_lookups += 1
                try:
                    self._resolve_detail(session, job, paths.get(job.external_id, ""))
                except requests.RequestException:
                    # A detail lookup is an accuracy enhancement, not grounds
                    # to throw away an otherwise complete board snapshot. Keep
                    # the row ambiguous so downstream fails closed safely.
                    pass

                after = classify_us_location(job.location or "", "workday")
                if after.status == AMBIGUOUS:
                    still_ambiguous += 1
                else:
                    classified_from_detail += 1

        notes = [facet_note] if facet_note else []
        if detail_lookups:
            notes.append(f"{detail_lookups} Workday detail lookup{'s' if detail_lookups != 1 else ''}")
        if classified_from_detail:
            notes.append(
                f"{classified_from_detail} ambiguous location{'s' if classified_from_detail != 1 else ''} classified from detail"
            )
        if still_ambiguous:
            notes.append(
                f"{still_ambiguous} relevant Workday location{'s' if still_ambiguous != 1 else ''} still ambiguous after detail"
            )
        if capped_ambiguous:
            notes.append(
                f"{capped_ambiguous} relevant ambiguous location{'s' if capped_ambiguous != 1 else ''} skipped by detail cap"
            )
        self.last_scan_note = ", ".join(notes)
        return jobs
