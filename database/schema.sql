-- Enable foreign key support in SQLite
PRAGMA foreign_keys = ON;

-- Table: rss_sources
CREATE TABLE IF NOT EXISTS rss_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link TEXT NOT NULL UNIQUE,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table: jobs
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    universal_id TEXT,
    job_link TEXT,
    job_description TEXT,
    status TEXT,
    job_match_score INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    application_link TEXT,
    rss_source_id INTEGER,
    structured_output TEXT,
    match_result TEXT,
    tailored_resume TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    tailored_resume_pdf_path TEXT,
    FOREIGN KEY (rss_source_id) REFERENCES rss_sources(id) ON DELETE SET NULL
);

-- Unique index for universal_id deduplication
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_universal_id ON jobs (universal_id);

-- Table: notes
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT,
    job_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

-- Triggers to auto-update modified_at timestamp on row updates
CREATE TRIGGER IF NOT EXISTS update_rss_sources_modified_at
AFTER UPDATE ON rss_sources
FOR EACH ROW
BEGIN
    UPDATE rss_sources SET modified_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_jobs_modified_at
AFTER UPDATE ON jobs
FOR EACH ROW
BEGIN
    UPDATE jobs SET modified_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_notes_modified_at
AFTER UPDATE ON notes
FOR EACH ROW
BEGIN
    UPDATE notes SET modified_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;
