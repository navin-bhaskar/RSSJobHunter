import sqlite3
import os
from typing import Optional, List, Dict, Any

DB_DIR = os.path.dirname(__file__)
DB_NAME = os.path.join(DB_DIR, "jobs.db")
SCHEMA_FILE = os.path.join(DB_DIR, "schema.sql")


def get_connection(db_name: str = DB_NAME) -> sqlite3.Connection:
    """Returns a connection to the SQLite database with foreign keys enabled."""
    conn = sqlite3.connect(db_name)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_name: str = DB_NAME, schema_file: str = SCHEMA_FILE) -> None:
    """Initializes the database schema from schema.sql and handles column migrations."""
    if not os.path.exists(schema_file):
        raise FileNotFoundError(f"Schema file not found at: {schema_file}")

    with open(schema_file, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_connection(db_name) as conn:
        cursor = conn.cursor()

        # Step 1: Execute single source of truth DDL schema (tables, indexes, triggers) from schema.sql
        cursor.executescript(schema_sql)

        # Step 2: Handle column migrations for pre-existing databases created on older schemas
        cursor.execute("PRAGMA table_info(jobs);")
        columns = [row[1] for row in cursor.fetchall()]
        if "rss_source_id" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN rss_source_id INTEGER REFERENCES rss_sources(id) ON DELETE SET NULL;")
        if "universal_id" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN universal_id TEXT;")
        if "structured_output" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN structured_output TEXT;")
        if "match_result" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN match_result TEXT;")
        if "tailored_resume" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN tailored_resume TEXT;")
        if "is_deleted" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0;")
        if "tailored_resume_pdf_path" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN tailored_resume_pdf_path TEXT;")

        conn.commit()


# --- RSS SOURCES CRUD ---

def create_rss_source(link: str, name: Optional[str] = None) -> int:
    """Inserts a new RSS source feed and returns its auto-incremented ID."""
    sql = "INSERT INTO rss_sources (link, name) VALUES (?, ?)"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (link, name))
        conn.commit()
        return cursor.lastrowid


def get_all_rss_sources() -> List[Dict[str, Any]]:
    """Retrieves all registered RSS sources."""
    sql = "SELECT * FROM rss_sources ORDER BY id ASC"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        return [dict(row) for row in cursor.fetchall()]


def get_rss_source(source_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a single RSS source by ID."""
    sql = "SELECT * FROM rss_sources WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (source_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_rss_source_by_link(link: str) -> Optional[Dict[str, Any]]:
    """Fetches an RSS source record by feed URL link."""
    sql = "SELECT * FROM rss_sources WHERE link = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (link,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_rss_source(source_id: int, link: str, name: Optional[str] = None) -> bool:
    """Updates an existing RSS source feed record."""
    sql = "UPDATE rss_sources SET link = ?, name = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (link, name, source_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_rss_source(source_id: int) -> bool:
    """Deletes an RSS source record."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rss_sources WHERE id = ?", (source_id,))
        conn.commit()
        return cursor.rowcount > 0


# --- JOBS CRUD ---

def create_job(
    job_link: str,
    job_description: str = "",
    status: str = "pending",
    job_match_score: int = 0,
    application_link: str = "",
    rss_source_id: Optional[int] = None,
    universal_id: Optional[str] = None,
    structured_output: Optional[str] = None,
) -> int:
    """Inserts a new job record with optional rss_source_id, universal_id, and structured_output, returning its ID."""
    sql = """
        INSERT INTO jobs (universal_id, job_link, job_description, status, job_match_score, application_link, rss_source_id, structured_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            sql, (universal_id, job_link, job_description, status, job_match_score, application_link, rss_source_id, structured_output)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_jobs(
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    rss_source_id: Optional[int] = None,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieves all jobs with optional status filter, search, or rss_source_id filter.

    Soft-deleted jobs (is_deleted = 1) are excluded unless include_deleted=True, so a
    removed job stays out of the UI but is still counted for Find Jobs dedup (it won't
    be re-fetched/re-created).
    """
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: List[Any] = []

    if not include_deleted:
        sql += " AND is_deleted = 0"

    if status_filter and status_filter.lower() != "all":
        sql += " AND status = ?"
        params.append(status_filter)

    if rss_source_id is not None:
        sql += " AND rss_source_id = ?"
        params.append(rss_source_id)

    if search:
        sql += " AND (job_link LIKE ? OR job_description LIKE ? OR application_link LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])

    sql += " ORDER BY id DESC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a single job by integer ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_job_by_universal_id(universal_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a single job record by its SHA-256 universal_id hash."""
    if not universal_id:
        return None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE universal_id = ?", (universal_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_job(
    job_id: int,
    job_link: str,
    job_description: str,
    status: str,
    job_match_score: int,
    application_link: str,
    rss_source_id: Optional[int] = None,
    universal_id: Optional[str] = None,
    structured_output: Optional[str] = None,
) -> bool:
    """Updates an existing job record."""
    sql = """
        UPDATE jobs
        SET universal_id = ?, job_link = ?, job_description = ?, status = ?, job_match_score = ?, application_link = ?, rss_source_id = ?, structured_output = ?
        WHERE id = ?
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            sql,
            (universal_id, job_link, job_description, status, job_match_score, application_link, rss_source_id, structured_output, job_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_job_structured_output(job_id: int, structured_output: str) -> bool:
    """Updates only the structured_output column for a job record."""
    sql = "UPDATE jobs SET structured_output = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (structured_output, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_match_result(job_id: int, match_result: str) -> bool:
    """Updates only the match_result column (full ATSMatchResult JSON) for a job record."""
    sql = "UPDATE jobs SET match_result = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (match_result, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_tailored_resume(job_id: int, tailored_resume: str) -> bool:
    """Updates only the tailored_resume column (full tailored ATSResumeSchema JSON) for a job record."""
    sql = "UPDATE jobs SET tailored_resume = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (tailored_resume, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_tailored_resume_pdf_path(job_id: int, pdf_path: str) -> bool:
    """Updates only the tailored_resume_pdf_path column for a job record."""
    sql = "UPDATE jobs SET tailored_resume_pdf_path = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (pdf_path, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_description(job_id: int, job_description: str) -> bool:
    """Updates only the job_description column for a job record."""
    sql = "UPDATE jobs SET job_description = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (job_description, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_match_score(job_id: int, job_match_score: int) -> bool:
    """Updates only the job_match_score column for a job record."""
    sql = "UPDATE jobs SET job_match_score = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (job_match_score, job_id))
        conn.commit()
        return cursor.rowcount > 0


def update_job_status(job_id: int, status: str) -> bool:
    """Updates only the status column for a job record."""
    sql = "UPDATE jobs SET status = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (status, job_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_job(job_id: int) -> bool:
    """Hard-deletes a job record (and associated notes via CASCADE). Used to roll back
    incomplete/dangling rows (e.g. a cancelled Find Jobs run), not for user removals."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def soft_delete_job(job_id: int) -> bool:
    """Marks a job as deleted without removing it, so it disappears from the UI but is
    still recognized as 'seen' and won't be re-fetched/re-created by Find Jobs."""
    sql = "UPDATE jobs SET is_deleted = 1 WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (job_id,))
        conn.commit()
        return cursor.rowcount > 0


# --- NOTES CRUD ---

def add_note(job_id: int, note_text: str) -> int:
    """Inserts a new note associated with a job_id."""
    sql = "INSERT INTO notes (job_id, note) VALUES (?, ?)"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (job_id, note_text))
        conn.commit()
        return cursor.lastrowid


def get_notes_for_job(job_id: int) -> List[Dict[str, Any]]:
    """Fetches all notes for a specific job."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notes WHERE job_id = ? ORDER BY id DESC", (job_id,))
        return [dict(row) for row in cursor.fetchall()]


def update_note(note_id: int, note_text: str) -> bool:
    """Updates an existing note's text."""
    sql = "UPDATE notes SET note = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (note_text, note_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_note(note_id: int) -> bool:
    """Deletes a note by ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_job_with_notes(job_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a job record along with all associated notes and source details if present."""
    job = get_job(job_id)
    if job:
        job["notes"] = get_notes_for_job(job_id)
        if job.get("rss_source_id"):
            job["rss_source"] = get_rss_source(job["rss_source_id"])
    return job


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
