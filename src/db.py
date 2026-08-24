import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from src.models import Job


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT,
    source TEXT,
    posted_at TEXT,
    description TEXT,
    score REAL DEFAULT 0,
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (company, external_id)
);

CREATE TABLE IF NOT EXISTS leads (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    result_url TEXT NOT NULL,
    title TEXT,
    snippet TEXT,
    query TEXT,
    kind TEXT DEFAULT 'other',
    score REAL DEFAULT 0,
    role_bucket TEXT DEFAULT '',
    author_name TEXT DEFAULT '',
    author_profile_url TEXT DEFAULT '',
    connection_type TEXT DEFAULT 'potential_connection',
    affiliations TEXT DEFAULT '[]',
    source_post_url TEXT DEFAULT '',
    relevance_reason TEXT DEFAULT '',
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company, external_id, result_url)
);

CREATE TABLE IF NOT EXISTS alert_queue (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (company, external_id)
);


CREATE TABLE IF NOT EXISTS networking_queue (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    notified_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (company, external_id)
);

CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_eligibility (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    eligible INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company, external_id)
);

CREATE TABLE IF NOT EXISTS tavily_credit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    kind TEXT NOT NULL,
    cache_key TEXT,
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class JobDB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        # DELETE is intentional: the GitHub workflow persists only jobs.db.
        # WAL could leave committed state in jobs.db-wal and cause duplicate alerts
        # after the state database is copied to the persistence branch.
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(leads)").fetchall()}
        if "kind" not in columns:
            self.conn.execute("ALTER TABLE leads ADD COLUMN kind TEXT DEFAULT 'other'")
        lead_columns = {
            "score": "REAL DEFAULT 0",
            "role_bucket": "TEXT DEFAULT ''",
            "author_name": "TEXT DEFAULT ''",
            "author_profile_url": "TEXT DEFAULT ''",
            "connection_type": "TEXT DEFAULT 'potential_connection'",
            "affiliations": "TEXT DEFAULT '[]'",
            "source_post_url": "TEXT DEFAULT ''",
            "relevance_reason": "TEXT DEFAULT ''",
        }
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(leads)").fetchall()}
        for name, declaration in lead_columns.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {declaration}")

    def record_tavily_credit(self, company: str, kind: str, cache_key: str = ""):
        """Conservatively log a Tavily Search attempt before the request is sent."""
        self.conn.execute(
            "INSERT INTO tavily_credit_log(company, kind, cache_key) VALUES (?, ?, ?)",
            (company, kind, cache_key),
        )
        self.conn.commit()

    def tavily_credits_used_today(self) -> int:
        return int(self.conn.execute(
            """
            SELECT COUNT(*) FROM tavily_credit_log
            WHERE used_at >= datetime('now', 'start of day')
            """
        ).fetchone()[0])

    def tavily_credits_used_last_24h(self) -> int:
        return int(self.conn.execute(
            """
            SELECT COUNT(*) FROM tavily_credit_log
            WHERE used_at >= datetime('now', '-24 hours')
            """
        ).fetchone()[0])

    def close(self):
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    @staticmethod
    def _key(job: Job):
        return (job.company, job.external_id)

    def sync_company(self, company: str, jobs: Iterable[Job]):
        jobs = list(jobs)
        current_ids = {j.external_id for j in jobs}

        previous_active = {
            row[0]
            for row in self.conn.execute(
                "SELECT external_id FROM jobs WHERE company=? AND is_active=1",
                (company,),
            ).fetchall()
        }
        existing_ids = {
            row[0]
            for row in self.conn.execute(
                "SELECT external_id FROM jobs WHERE company=?",
                (company,),
            ).fetchall()
        }

        self.conn.execute("UPDATE jobs SET is_active=0 WHERE company=?", (company,))

        new_jobs = []
        for j in jobs:
            self.conn.execute(
                """
                INSERT INTO jobs(company, external_id, title, location, url, source, posted_at, description, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company, external_id) DO UPDATE SET
                    title=excluded.title,
                    location=excluded.location,
                    url=excluded.url,
                    source=excluded.source,
                    posted_at=excluded.posted_at,
                    description=excluded.description,
                    score=excluded.score,
                    last_seen=CURRENT_TIMESTAMP,
                    is_active=1
                """,
                (
                    j.company, j.external_id, j.title, j.location, j.url, j.source,
                    j.posted_at, j.description, j.score,
                ),
            )
            if j.external_id not in existing_ids:
                new_jobs.append(j)

        self.conn.commit()
        disappeared = len(previous_active - current_ids)
        return new_jobs, disappeared

    def upsert_and_get_new(self, jobs: Iterable[Job]):
        jobs = list(jobs)
        by_company = {}
        for job in jobs:
            by_company.setdefault(job.company, []).append(job)
        new = []
        for company, company_jobs in by_company.items():
            company_new, _ = self.sync_company(company, company_jobs)
            new.extend(company_new)
        return new


    def set_eligibility(self, job: Job, eligible: bool, reason: str = "", *, baseline: bool = False) -> bool:
        """Persist alert eligibility and return True only on a newly eligible transition.

        ``baseline=True`` is used by --seed so existing jobs establish state
        without becoming alert candidates. This also means a later location or
        title correction from ineligible -> eligible can legitimately alert.
        """
        row = self.conn.execute(
            "SELECT eligible FROM job_eligibility WHERE company=? AND external_id=?",
            (job.company, job.external_id),
        ).fetchone()
        previous = None if row is None else bool(row[0])
        self.conn.execute(
            """
            INSERT INTO job_eligibility(company, external_id, eligible, reason, evaluated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(company, external_id) DO UPDATE SET
                eligible=excluded.eligible,
                reason=excluded.reason,
                evaluated_at=CURRENT_TIMESTAMP
            """,
            (job.company, job.external_id, 1 if eligible else 0, str(reason)[:500]),
        )
        self.conn.commit()
        if baseline:
            return False
        return bool(eligible and previous is not True)

    def mark_alert_skipped(self, job: Job, reason: str):
        self.conn.execute(
            """
            UPDATE alert_queue
            SET delivered_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=?
            WHERE company=? AND external_id=?
            """,
            (f"skipped: {str(reason)[:900]}", job.company, job.external_id),
        )
        self.conn.commit()

    def mark_networking_skipped(self, job: Job, reason: str):
        self.conn.execute(
            """
            UPDATE networking_queue
            SET completed_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=?
            WHERE company=? AND external_id=?
            """,
            (f"skipped: {str(reason)[:900]}", job.company, job.external_id),
        )
        self.conn.commit()

    def enqueue_alert(self, job: Job):
        self.conn.execute(
            "INSERT OR IGNORE INTO alert_queue(company, external_id) VALUES (?, ?)",
            (job.company, job.external_id),
        )
        self.conn.commit()

    def pending_alert_jobs(self, limit: int = 20):
        rows = self.conn.execute(
            """
            SELECT j.company, j.external_id, j.title, j.location, j.url, j.source,
                   j.posted_at, j.description, j.score
            FROM alert_queue q
            JOIN jobs j ON j.company=q.company AND j.external_id=q.external_id
            WHERE q.delivered_at IS NULL
            ORDER BY q.created_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [Job(*row) for row in rows]

    def mark_alert_sent(self, job: Job):
        self.conn.execute(
            """
            UPDATE alert_queue
            SET delivered_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=NULL
            WHERE company=? AND external_id=?
            """,
            (job.company, job.external_id),
        )
        self.conn.commit()

    def mark_alert_error(self, job: Job, error: str):
        self.conn.execute(
            """
            UPDATE alert_queue
            SET attempts=attempts+1, last_error=?
            WHERE company=? AND external_id=?
            """,
            (str(error)[:1000], job.company, job.external_id),
        )
        self.conn.commit()

    def pending_alert_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM alert_queue WHERE delivered_at IS NULL"
        ).fetchone()[0])

    def enqueue_networking(self, job: Job):
        self.conn.execute(
            "INSERT OR IGNORE INTO networking_queue(company, external_id) VALUES (?, ?)",
            (job.company, job.external_id),
        )
        self.conn.commit()

    def networking_due_jobs(self, limit: int = 20):
        rows = self.conn.execute(
            """
            SELECT j.company, j.external_id, j.title, j.location, j.url, j.source,
                   j.posted_at, j.description, j.score
            FROM networking_queue q
            JOIN jobs j ON j.company=q.company AND j.external_id=q.external_id
            WHERE q.completed_at IS NULL
            ORDER BY q.created_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [Job(*row) for row in rows]

    def mark_networking_complete(self, job: Job):
        self.conn.execute(
            """
            UPDATE networking_queue
            SET completed_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=NULL
            WHERE company=? AND external_id=?
            """,
            (job.company, job.external_id),
        )
        self.conn.commit()

    def mark_networking_deferred(self, job: Job, reason: str = ""):
        self.conn.execute(
            """
            UPDATE networking_queue
            SET last_error=?
            WHERE company=? AND external_id=?
            """,
            (str(reason)[:1000], job.company, job.external_id),
        )
        self.conn.commit()

    def mark_networking_error(self, job: Job, error: str):
        self.conn.execute(
            """
            UPDATE networking_queue
            SET attempts=attempts+1, last_error=?
            WHERE company=? AND external_id=?
            """,
            (str(error)[:1000], job.company, job.external_id),
        )
        self.conn.commit()

    def networking_is_complete(self, job: Job) -> bool:
        row = self.conn.execute(
            "SELECT completed_at FROM networking_queue WHERE company=? AND external_id=?",
            (job.company, job.external_id),
        ).fetchone()
        return bool(row and row[0])

    def alert_is_sent(self, job: Job) -> bool:
        row = self.conn.execute(
            "SELECT delivered_at FROM alert_queue WHERE company=? AND external_id=?",
            (job.company, job.external_id),
        ).fetchone()
        return bool(row and row[0])

    def networking_notified_count(self, job: Job) -> int:
        row = self.conn.execute(
            "SELECT notified_count FROM networking_queue WHERE company=? AND external_id=?",
            (job.company, job.external_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def set_networking_notified_count(self, job: Job, count: int):
        self.conn.execute(
            """
            UPDATE networking_queue SET notified_count=?
            WHERE company=? AND external_id=?
            """,
            (max(0, int(count)), job.company, job.external_id),
        )
        self.conn.commit()

    def get_leads(self, job: Job):
        from src.enrich import Lead
        rows = self.conn.execute(
            """
            SELECT result_url, title, snippet, query, kind, score, role_bucket,
                   author_name, author_profile_url, connection_type, affiliations,
                   source_post_url, relevance_reason
            FROM leads
            WHERE company=? AND external_id=?
            ORDER BY score DESC, discovered_at ASC
            """,
            (job.company, job.external_id),
        ).fetchall()
        leads = []
        for row in rows:
            try:
                affiliations = json.loads(row[10] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                affiliations = []
            leads.append(Lead(
                url=row[0], title=row[1] or "", snippet=row[2] or "",
                query=row[3] or "", kind=row[4] or "other", score=float(row[5] or 0),
                role_bucket=row[6] or "", author_name=row[7] or "",
                author_profile_url=row[8] or "", connection_type=row[9] or "potential_connection",
                affiliations=tuple(affiliations) if isinstance(affiliations, list) else (),
                source_post_url=row[11] or "", relevance_reason=row[12] or "",
            ))
        return leads

    def add_lead(
        self,
        job: Job,
        result_url: str,
        title: str,
        snippet: str,
        query: str,
        kind: str = "other",
        *,
        score: float = 0.0,
        role_bucket: str = "",
        author_name: str = "",
        author_profile_url: str = "",
        connection_type: str = "potential_connection",
        affiliations=(),
        source_post_url: str = "",
        relevance_reason: str = "",
    ):
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO leads(
                company, external_id, result_url, title, snippet, query, kind,
                score, role_bucket, author_name, author_profile_url, connection_type,
                affiliations, source_post_url, relevance_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.company, job.external_id, result_url, title, snippet, query, kind,
                float(score or 0), role_bucket, author_name, author_profile_url,
                connection_type, json.dumps(list(affiliations or ())), source_post_url,
                relevance_reason,
            ),
        )
        inserted = cur.rowcount == 1
        if not inserted:
            self.conn.execute(
                """
                UPDATE leads SET
                    title=?, snippet=?, query=?, kind=?, score=?, role_bucket=?,
                    author_name=?, author_profile_url=?, connection_type=?, affiliations=?,
                    source_post_url=?, relevance_reason=?
                WHERE company=? AND external_id=? AND result_url=?
                """,
                (
                    title, snippet, query, kind, float(score or 0), role_bucket,
                    author_name, author_profile_url, connection_type,
                    json.dumps(list(affiliations or ())), source_post_url, relevance_reason,
                    job.company, job.external_id, result_url,
                ),
            )
        self.conn.commit()
        return inserted

    def add_lead_record(self, job: Job, lead) -> bool:
        return self.add_lead(
            job,
            lead.url,
            lead.title,
            lead.snippet,
            lead.query,
            lead.kind,
            score=lead.score,
            role_bucket=lead.role_bucket,
            author_name=lead.author_name,
            author_profile_url=lead.author_profile_url,
            connection_type=lead.connection_type,
            affiliations=lead.affiliations,
            source_post_url=lead.source_post_url,
            relevance_reason=lead.relevance_reason,
        )

    def get_cached_search(self, cache_key: str, ttl_hours: int):
        row = self.conn.execute(
            "SELECT response_json, cached_at FROM search_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        try:
            cached_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - cached_at > timedelta(hours=max(0, ttl_hours)):
                return None
            value = json.loads(row[0])
            return value if isinstance(value, list) else None
        except Exception:
            return None

    def put_cached_search(self, cache_key: str, results):
        self.conn.execute(
            """
            INSERT INTO search_cache(cache_key, response_json, cached_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json=excluded.response_json,
                cached_at=CURRENT_TIMESTAMP
            """,
            (cache_key, json.dumps(results or [])),
        )
        self.conn.commit()
