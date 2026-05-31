import hashlib
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT    UNIQUE NOT NULL,
    title       TEXT    NOT NULL,
    company     TEXT    NOT NULL,
    location    TEXT,
    experience  TEXT,
    salary      TEXT,
    link        TEXT    NOT NULL,
    source      TEXT,
    is_walkin   INTEGER DEFAULT 0,
    description TEXT,
    posted_date TEXT,
    date_added  TEXT    NOT NULL,
    alert_sent  INTEGER DEFAULT 0,
    alert_sent_at TEXT
);
"""

CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    source      TEXT,
    jobs_found  INTEGER DEFAULT 0,
    jobs_new    INTEGER DEFAULT 0,
    jobs_sent   INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'running',
    error       TEXT
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(hash);
"""


# ── Connection ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        conn.execute(CREATE_JOBS_TABLE)
        conn.execute(CREATE_RUNS_TABLE)
        conn.execute(CREATE_INDEX)
    logger.info("Database initialised at %s", config.DATABASE_PATH)


# ── Hash ──────────────────────────────────────────────────────────────────────

def compute_hash(title: str, company: str, link: str) -> str:
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{link.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Job operations ────────────────────────────────────────────────────────────

def job_exists(job_hash: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE hash = ?", (job_hash,)
        ).fetchone()
        return row is not None


def save_job(job: dict) -> Optional[int]:
    """
    Insert job if new. Returns row id on success, None if duplicate.
    """
    h = compute_hash(
        job.get("title", ""),
        job.get("company", ""),
        job.get("link", ""),
    )
    if job_exists(h):
        return None

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs
                (hash, title, company, location, experience, salary,
                 link, source, is_walkin, description, posted_date,
                 date_added, alert_sent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                h,
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("experience", ""),
                job.get("salary", ""),
                job.get("link", ""),
                job.get("source", ""),
                1 if job.get("is_walkin") else 0,
                job.get("description", "")[:2000],
                job.get("posted_date", ""),
                now,
            ),
        )
        logger.debug("Saved new job id=%d  %s @ %s", cur.lastrowid,
                     job.get("title"), job.get("company"))
        return cur.lastrowid


def save_jobs_bulk(jobs: list[dict]) -> tuple[int, int]:
    """Returns (new_count, duplicate_count)."""
    new, dupes = 0, 0
    for j in jobs:
        result = save_job(j)
        if result:
            new += 1
        else:
            dupes += 1
    return new, dupes


def get_unsent_jobs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE alert_sent = 0 ORDER BY date_added DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_sent(job_id: int):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET alert_sent = 1, alert_sent_at = ? WHERE id = ?",
            (now, job_id),
        )


def get_stats() -> dict:
    with get_conn() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        sent    = conn.execute("SELECT COUNT(*) FROM jobs WHERE alert_sent=1").fetchone()[0]
        walkins = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_walkin=1").fetchone()[0]
        return {"total": total, "sent": sent, "pending": total - sent, "walkins": walkins}


# ── Run audit ─────────────────────────────────────────────────────────────────

def start_run(source: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scrape_runs (started_at, source, status) VALUES (?,?,'running')",
            (now, source),
        )
        return cur.lastrowid


def finish_run(run_id: int, found: int, new: int, sent: int, error: str = None):
    now = datetime.now(timezone.utc).isoformat()
    status = "failed" if error else "success"
    with get_conn() as conn:
        conn.execute(
            """UPDATE scrape_runs
               SET finished_at=?, jobs_found=?, jobs_new=?, jobs_sent=?,
                   status=?, error=?
               WHERE id=?""",
            (now, found, new, sent, status, error, run_id),
        )
