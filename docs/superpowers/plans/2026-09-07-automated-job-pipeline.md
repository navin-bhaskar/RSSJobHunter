# Automated Job Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual per-job "Enrich"/"Match" buttons with a single automatic "Find Jobs" pipeline that fetches RSS feeds, dedupes in code, extracts structured job data via LLM, scores each job against the candidate's resume via ATS matching, and generates a tailored resume for jobs that clear a configurable score threshold — with results visible in a color-coded, score-sorted jobs table.

**Architecture:** A new `FindJobsWorker` QThread in `gui/main_window.py` replaces `RSSFetchWorker`/`EnrichJobWorker`/`MatchResumeWorker`, orchestrating three agents per newly-discovered job in sequence (`rss_agent` → `matcher_agent` → conditionally a new `tailor_agent`). Two new DB columns (`match_result`, `tailored_resume`) persist the full LLM outputs. A code-level preflight check (API key + resume file existence) blocks the whole pipeline before it starts; per-job LLM failures are caught and skipped without aborting the run.

**Tech Stack:** Python 3.12, PyQt6, OpenAI Agents SDK (`openai-agents`), Pydantic v2, SQLite3 (stdlib), `python-dotenv`.

**Spec:** `docs/superpowers/specs/2026-09-07-automated-job-pipeline-design.md`

## Global Constraints

- No new test framework is introduced. This project has zero existing test
  infrastructure (no `tests/` dir, no pytest dependency) — agents are smoke-tested
  via their own `__main__` blocks. Every task below verifies itself with an ad-hoc
  Python smoke script run via the Bash tool, matching that existing convention.
- No GUI settings screen for the match threshold — `MATCH_SCORE_THRESHOLD` is an
  env var only (default `70`), read from `.env` via `os.getenv`.
- Preflight failures (missing/placeholder `OPENAI_API_KEY`, missing resume file)
  block the entire pipeline before the worker thread starts. A single job's LLM
  failure during the run is caught, logged to the progress signal, and skipped —
  the run continues to the next job.
- New agent output reuses existing Pydantic schemas — `TailorAgent` produces
  `agents.resume_agent.schema.ATSResumeSchema`, no new schema file.
- Deduplication happens in code (SHA-256 `universal_id`, existing
  `tools.get_universal_id` helper) *before* any LLM call — never call `rss_agent`
  for a job whose `universal_id` or `job_link` is already in the `jobs` table.
- Manual per-job Enrich/Match buttons and their worker classes from the previous
  session are removed entirely — the pipeline is fully automatic via one "Find
  Jobs" action.

---

### Task 1: Database layer — `match_result` and `tailored_resume` columns

**Files:**
- Modify: `database/schema.sql`
- Modify: `database/database.py`
- Modify: `database/__init__.py`
- Test: ad-hoc script run via Bash (no persistent test file — this project has no `tests/` dir)

**Interfaces:**
- Produces: `update_job_match_result(job_id: int, match_result: str) -> bool`,
  `update_job_tailored_resume(job_id: int, tailored_resume: str) -> bool` — both
  importable from `database`. Later tasks (3, 4) call these by exact name.
- Produces: `jobs` table gains `match_result TEXT` and `tailored_resume TEXT`
  columns, both nullable, defaulting to `NULL`.

- [ ] **Step 1: Add the two columns to the schema DDL**

Edit `database/schema.sql`. Find the `jobs` table definition:

```sql
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
    FOREIGN KEY (rss_source_id) REFERENCES rss_sources(id) ON DELETE SET NULL
);
```

Replace it with:

```sql
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
    FOREIGN KEY (rss_source_id) REFERENCES rss_sources(id) ON DELETE SET NULL
);
```

- [ ] **Step 2: Add migration checks for existing databases**

Edit `database/database.py`. In `init_db`, find:

```python
        if "structured_output" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN structured_output TEXT;")

        conn.commit()
```

Replace with:

```python
        if "structured_output" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN structured_output TEXT;")
        if "match_result" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN match_result TEXT;")
        if "tailored_resume" not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN tailored_resume TEXT;")

        conn.commit()
```

- [ ] **Step 3: Add the two new update functions**

Edit `database/database.py`. Find:

```python
def update_job_structured_output(job_id: int, structured_output: str) -> bool:
    """Updates only the structured_output column for a job record."""
    sql = "UPDATE jobs SET structured_output = ? WHERE id = ?"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (structured_output, job_id))
        conn.commit()
        return cursor.rowcount > 0
```

Insert immediately after it:

```python


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
```

- [ ] **Step 4: Export the new functions**

Edit `database/__init__.py`. Find:

```python
from .database import (
    get_connection,
    init_db,
    create_rss_source,
    get_all_rss_sources,
    get_rss_source,
    get_rss_source_by_link,
    update_rss_source,
    delete_rss_source,
    create_job,
    get_all_jobs,
    get_job,
    get_job_by_universal_id,
    update_job,
    update_job_description,
    update_job_match_score,
    update_job_structured_output,
    delete_job,
    add_note,
    get_notes_for_job,
    update_note,
    delete_note,
    get_job_with_notes,
    DB_NAME,
    SCHEMA_FILE,
)

__all__ = [
    "get_connection",
    "init_db",
    "create_rss_source",
    "get_all_rss_sources",
    "get_rss_source",
    "get_rss_source_by_link",
    "update_rss_source",
    "delete_rss_source",
    "create_job",
    "get_all_jobs",
    "get_job",
    "get_job_by_universal_id",
    "update_job",
    "update_job_description",
    "update_job_match_score",
    "update_job_structured_output",
    "delete_job",
    "add_note",
    "get_notes_for_job",
    "update_note",
    "delete_note",
    "get_job_with_notes",
    "DB_NAME",
    "SCHEMA_FILE",
]
```

Replace with:

```python
from .database import (
    get_connection,
    init_db,
    create_rss_source,
    get_all_rss_sources,
    get_rss_source,
    get_rss_source_by_link,
    update_rss_source,
    delete_rss_source,
    create_job,
    get_all_jobs,
    get_job,
    get_job_by_universal_id,
    update_job,
    update_job_description,
    update_job_match_score,
    update_job_structured_output,
    update_job_match_result,
    update_job_tailored_resume,
    delete_job,
    add_note,
    get_notes_for_job,
    update_note,
    delete_note,
    get_job_with_notes,
    DB_NAME,
    SCHEMA_FILE,
)

__all__ = [
    "get_connection",
    "init_db",
    "create_rss_source",
    "get_all_rss_sources",
    "get_rss_source",
    "get_rss_source_by_link",
    "update_rss_source",
    "delete_rss_source",
    "create_job",
    "get_all_jobs",
    "get_job",
    "get_job_by_universal_id",
    "update_job",
    "update_job_description",
    "update_job_match_score",
    "update_job_structured_output",
    "update_job_match_result",
    "update_job_tailored_resume",
    "delete_job",
    "add_note",
    "get_notes_for_job",
    "update_note",
    "delete_note",
    "get_job_with_notes",
    "DB_NAME",
    "SCHEMA_FILE",
]
```

- [ ] **Step 5: Write and run the verification script**

Run via Bash from the project root:

```bash
python -c "
from database import init_db, create_job, update_job_match_result, update_job_tailored_resume, get_job, delete_job

init_db()

job_id = create_job(job_link='https://example.com/verify-task1', job_description='desc', status='pending')
assert get_job(job_id)['match_result'] is None
assert get_job(job_id)['tailored_resume'] is None

assert update_job_match_result(job_id, '{\"match_score\": 85}') is True
assert update_job_tailored_resume(job_id, '{\"professional_summary\": \"test\"}') is True

job = get_job(job_id)
assert job['match_result'] == '{\"match_score\": 85}', job['match_result']
assert job['tailored_resume'] == '{\"professional_summary\": \"test\"}', job['tailored_resume']

delete_job(job_id)
print('ALL PASS')
"
```

Expected output: `ALL PASS`. If it fails with a missing-column error, re-check
Step 1/2 were applied to the schema file the running `jobs.db` was already
initialized from — `init_db()` runs the migration checks on every call, so this
should self-heal for an existing `jobs.db`, but confirm no exception was raised.

- [ ] **Step 6: Commit**

```bash
git add database/schema.sql database/database.py database/__init__.py
git commit -m "feat: add match_result and tailored_resume columns to jobs table"
```

---

### Task 2: New agent — `agents/tailor_agent/`

**Files:**
- Create: `agents/tailor_agent/__init__.py`
- Create: `agents/tailor_agent/agent.py`
- Modify: `agents/__init__.py`
- Test: ad-hoc script run via Bash

**Interfaces:**
- Consumes: `agents.resume_agent.schema.ATSResumeSchema` (existing, from Task-independent code already in the repo).
- Produces: `agents.tailor_agent.TailorAgent` with method
  `tailor_resume(resume_data: ATSResumeSchema, job_description: str, job_title: Optional[str] = None, company_name: Optional[str] = None) -> ATSResumeSchema`.
  Also `agents.tailor_agent.tailor_resume_for_job(resume_data, job_description, job_title=None, company_name=None) -> ATSResumeSchema`.
  Both re-exported from the top-level `agents` package. Task 3's `FindJobsWorker`
  imports `TailorAgent` from `agents` by this exact name.

- [ ] **Step 1: Create the agent module**

Create `agents/tailor_agent/agent.py`:

```python
"""
Resume Tailoring Agent using OpenAI Agents SDK.
Rewrites a candidate's structured resume to emphasize the skills, experience,
and language most relevant to a specific target job posting, without
fabricating any facts.
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv


def _get_openai_agents_sdk():
    """Dynamically imports Agent and Runner from the installed openai-agents package."""
    orig_path = sys.path.copy()
    saved_agents_mod = sys.modules.get("agents")
    if "agents" in sys.modules:
        del sys.modules["agents"]

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path = [
        p for p in sys.path
        if os.path.abspath(p) != project_root
        and os.path.abspath(p) != os.path.abspath(".")
        and p not in ("", ".")
    ]
    try:
        import agents as _sdk
        agent_cls = _sdk.Agent
        runner_cls = _sdk.Runner
        return agent_cls, runner_cls
    finally:
        sys.path = orig_path
        if saved_agents_mod is not None:
            sys.modules["agents"] = saved_agents_mod


Agent, Runner = _get_openai_agents_sdk()


from agents.resume_agent.schema import ATSResumeSchema


class TailorAgent:
    """
    Agent that rewrites a candidate's structured resume to target a specific
    job posting, emphasizing relevant skills and experience while preserving
    all factual details (names, companies, dates, degrees, titles).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")

        if self.api_key and self.api_key != "your_openai_api_key_here":
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url

    def tailor_resume(
        self,
        resume_data: ATSResumeSchema,
        job_description: str,
        job_title: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> ATSResumeSchema:
        """
        Rewrites a structured resume to target the given job posting.

        Args:
            resume_data: The candidate's base structured resume (ATSResumeSchema).
            job_description: Full text of the target job posting.
            job_title: Optional title of the target position.
            company_name: Optional hiring company name.

        Returns:
            A new ATSResumeSchema with professional_summary, work_experience
            responsibilities, and skills ordering rewritten to target the job,
            with all factual details preserved unchanged.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OpenAI API key is missing or invalid. Please configure OPENAI_API_KEY in your .env file."
            )

        resume_json_str = resume_data.model_dump_json(indent=2)

        system_prompt = (
            "You are an elite resume writer and ATS optimization specialist. "
            "Your task is to rewrite a candidate's structured resume to target a specific "
            "job posting, following the exact same output schema as the input.\n\n"
            "Rules:\n"
            "- NEVER fabricate or alter facts: contact_info, company names, job titles, "
            "employment dates, education, and certifications must remain exactly as given.\n"
            "- Rewrite `professional_summary` to highlight the 2-3 qualifications most "
            "relevant to this specific job posting.\n"
            "- Rewrite each work_experience entry's `responsibilities` bullet points to "
            "emphasize achievements and technologies relevant to the target job, using "
            "the job posting's own terminology where truthfully applicable. Do not invent "
            "responsibilities the candidate did not have.\n"
            "- Reorder `skills` categories (technical_skills, tools_and_platforms, "
            "databases, soft_skills) so the skills most relevant to this job appear first "
            "within each list. Do not add skills not present in the original resume.\n"
            "- Leave `education`, `projects`, `certifications`, `languages`, and "
            "`total_years_experience` unchanged unless correcting an obvious extraction error."
        )

        user_prompt = (
            f"=== TARGET JOB POSTING ===\n"
            f"Role Title: {job_title or 'Not specified'}\n"
            f"Company: {company_name or 'Not specified'}\n\n"
            f"Job Description:\n{job_description}\n\n"
            f"=== CANDIDATE'S BASE RESUME (STRUCTURED ATS FORMAT) ===\n"
            f"{resume_json_str}\n\n"
            f"Rewrite this resume to target the job posting above, following the rules exactly."
        )

        agent = Agent(
            name="ResumeTailorAgent",
            instructions=system_prompt,
            model=self.model,
            output_type=ATSResumeSchema,
        )

        result = Runner.run_sync(
            starting_agent=agent,
            input=user_prompt,
        )

        parsed = result.final_output
        if not isinstance(parsed, ATSResumeSchema):
            if isinstance(parsed, dict):
                parsed = ATSResumeSchema.model_validate(parsed)
            elif isinstance(parsed, str):
                parsed = ATSResumeSchema.model_validate_json(parsed)

        return parsed


def tailor_resume_for_job(
    resume_data: ATSResumeSchema,
    job_description: str,
    job_title: Optional[str] = None,
    company_name: Optional[str] = None,
) -> ATSResumeSchema:
    """
    Convenience function to run the Resume Tailor Agent.
    """
    agent = TailorAgent()
    return agent.tailor_resume(
        resume_data=resume_data,
        job_description=job_description,
        job_title=job_title,
        company_name=company_name,
    )


if __name__ == "__main__":
    import json as _json

    if len(sys.argv) < 3:
        print("Usage: python -m agents.tailor_agent.agent <resume_json_path> <job_description_txt_path>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sample_resume = ATSResumeSchema.model_validate(_json.load(f))
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        sample_jd = f.read()

    tailored = tailor_resume_for_job(sample_resume, sample_jd)
    print("=== Tailored Resume ===")
    print(tailored.model_dump_json(indent=2))
```

- [ ] **Step 2: Create the package `__init__.py`**

Create `agents/tailor_agent/__init__.py`:

```python
"""
Resume Tailoring Agent package for RSS Job Hunter.
"""

from .agent import TailorAgent, tailor_resume_for_job

__all__ = [
    "TailorAgent",
    "tailor_resume_for_job",
]
```

- [ ] **Step 3: Register in the top-level `agents` package**

Edit `agents/__init__.py`. Find:

```python
from .rss_agent import (
    RSSJobAgent,
    StructuredJobListingSchema,
    StructuredFeedSchema,
    StructuredJobListing,
    StructuredFeedOutput,
    process_rss_feed,
)
from .resume_agent import ResumeAgent, parse_resume_ats
from .matcher_agent import MatcherAgent, match_resume_to_job

__all__ = [
    "RSSJobAgent",
    "StructuredJobListingSchema",
    "StructuredFeedSchema",
    "StructuredJobListing",
    "StructuredFeedOutput",
    "process_rss_feed",
    "ResumeAgent",
    "parse_resume_ats",
    "MatcherAgent",
    "match_resume_to_job",
]
```

Replace with:

```python
from .rss_agent import (
    RSSJobAgent,
    StructuredJobListingSchema,
    StructuredFeedSchema,
    StructuredJobListing,
    StructuredFeedOutput,
    process_rss_feed,
)
from .resume_agent import ResumeAgent, parse_resume_ats
from .matcher_agent import MatcherAgent, match_resume_to_job
from .tailor_agent import TailorAgent, tailor_resume_for_job

__all__ = [
    "RSSJobAgent",
    "StructuredJobListingSchema",
    "StructuredFeedSchema",
    "StructuredJobListing",
    "StructuredFeedOutput",
    "process_rss_feed",
    "ResumeAgent",
    "parse_resume_ats",
    "MatcherAgent",
    "match_resume_to_job",
    "TailorAgent",
    "tailor_resume_for_job",
]
```

- [ ] **Step 4: Write and run the verification script**

This cannot make a real LLM call without a funded API key, so it verifies
import wiring and the pre-call guard (the same level of testing the sibling
agents get via their own `__main__` blocks — none of them have automated
tests either).

```bash
python -c "
import os
from unittest.mock import patch

import agents
assert agents.TailorAgent is agents.tailor_agent.TailorAgent
assert callable(agents.tailor_resume_for_job)

from agents.tailor_agent import TailorAgent
from agents.resume_agent.schema import ATSResumeSchema, ContactInfo, CategorizedSkills

dummy_resume = ATSResumeSchema(
    contact_info=ContactInfo(full_name='Test Candidate'),
    skills=CategorizedSkills(),
)

with patch.dict(os.environ, {'OPENAI_API_KEY': 'your_openai_api_key_here'}, clear=False):
    agent = TailorAgent(api_key=None)
    try:
        agent.tailor_resume(dummy_resume, 'some job description')
        raise SystemExit('expected ValueError for missing API key, none raised')
    except ValueError as e:
        assert 'OpenAI API key' in str(e), str(e)

print('ALL PASS')
"
```

Expected output: `ALL PASS`.

- [ ] **Step 5: Commit**

```bash
git add agents/tailor_agent agents/__init__.py
git commit -m "feat: add TailorAgent for job-targeted resume generation"
```

---

### Task 3: `FindJobsWorker` pipeline (added alongside existing code, not yet wired to the UI)

**Files:**
- Modify: `gui/main_window.py` (additive only — old `RSSFetchWorker`, `EnrichJobWorker`, `MatchResumeWorker` classes and their buttons stay in place for now; Task 4 removes them)
- Modify: `.env.example`
- Test: ad-hoc script run via Bash

**Interfaces:**
- Consumes: `database.update_job_match_result`, `database.update_job_tailored_resume`
  (Task 1); `agents.TailorAgent` (Task 2); `agents.RSSJobAgent`, `agents.ResumeAgent`,
  `agents.MatcherAgent` (pre-existing).
- Produces: `gui.main_window.check_pipeline_prerequisites() -> Optional[str]` (module-level
  function, returns an error message string or `None`). `gui.main_window.FindJobsWorker`
  (QThread) with signals `progress = pyqtSignal(str)` and
  `finished = pyqtSignal(int, int, int, int, int)` emitting
  `(total_fetched, total_new, total_matched, total_tailored, total_errors)`.
  Task 4's UI wiring consumes both by these exact names.

- [ ] **Step 1: Add the threshold setting to `.env.example`**

Edit `.env.example`. Find:

```
# Candidate Resume PDF Configuration
# Specify the filename or absolute/relative path to your resume PDF
RESUME_PATH=resume.pdf
# Alias (also supported):
# RESUME=resume.pdf
```

Replace with:

```
# Candidate Resume PDF Configuration
# Specify the filename or absolute/relative path to your resume PDF
RESUME_PATH=resume.pdf
# Alias (also supported):
# RESUME=resume.pdf

# Minimum ATS match score (0-100) a job must reach before a tailored resume
# is automatically generated for it during "Find Jobs". Matches the "Good
# Match" tier boundary in the ATS scoring rubric.
MATCH_SCORE_THRESHOLD=70
```

- [ ] **Step 2: Add required imports to `gui/main_window.py`**

Edit `gui/main_window.py`. Find:

```python
import json
import sys
from typing import Optional, List, Dict, Any
```

Replace with:

```python
import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
```

Then find:

```python
from database import (
    init_db,
    get_all_jobs,
    get_job,
    get_job_with_notes,
    create_job,
    add_note,
    get_all_rss_sources,
    update_job_structured_output,
    update_job_match_score,
)
```

Replace with:

```python
from database import (
    init_db,
    get_all_jobs,
    get_job,
    get_job_with_notes,
    create_job,
    add_note,
    get_all_rss_sources,
    update_job_match_score,
    update_job_match_result,
    update_job_tailored_resume,
)
```

(`update_job_structured_output` is dropped here — `FindJobsWorker` passes
`structured_output` directly to `create_job` at insert time rather than
updating it afterward. Task 4 removes the last caller of the old
per-job-update flow, so this import would otherwise go unused.)

- [ ] **Step 3: Add the preflight check function and `FindJobsWorker` class**

Edit `gui/main_window.py`. Find the end of the existing `MatchResumeWorker` class
(right before `class MainWindow(QMainWindow):`):

```python
        try:
            from agents import ResumeAgent, MatcherAgent

            resume_schema, _from_cache = ResumeAgent().parse_resume()
            result = MatcherAgent().evaluate_match(
                resume_data=resume_schema,
                job_description=self.job.get("job_description") or "",
            )
            self.finished.emit(self.job["id"], result, "")
        except Exception as e:
            self.finished.emit(self.job["id"], None, str(e))


class MainWindow(QMainWindow):
```

Insert the new function and class between them:

```python
        try:
            from agents import ResumeAgent, MatcherAgent

            resume_schema, _from_cache = ResumeAgent().parse_resume()
            result = MatcherAgent().evaluate_match(
                resume_data=resume_schema,
                job_description=self.job.get("job_description") or "",
            )
            self.finished.emit(self.job["id"], result, "")
        except Exception as e:
            self.finished.emit(self.job["id"], None, str(e))


def check_pipeline_prerequisites() -> Optional[str]:
    """
    Validates required AI pipeline configuration before starting FindJobsWorker.

    Returns:
        An error message string if a prerequisite is missing, or None if all
        checks pass.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_openai_api_key_here":
        return (
            "OPENAI_API_KEY is not configured. Please set a valid OpenAI API key "
            "in your .env file before running Find Jobs."
        )

    resume_path = os.getenv("RESUME_PATH") or os.getenv("RESUME", "resume.pdf")
    if not Path(resume_path).exists():
        return (
            f"Resume file not found at '{resume_path}'. Please configure RESUME_PATH "
            "in your .env file and ensure the PDF exists before running Find Jobs."
        )

    return None


class FindJobsWorker(QThread):
    """
    Worker thread that fetches RSS feeds, deduplicates new items in code, extracts
    structured job data via the LLM rss_agent, scores each new job against the
    candidate's resume via the LLM matcher_agent, and generates a tailored resume
    via the LLM tailor_agent for any job whose score clears MATCH_SCORE_THRESHOLD.
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int, int, int, int)
    # total_fetched, total_new, total_matched, total_tailored, total_errors

    def run(self) -> None:
        from agents import RSSJobAgent, ResumeAgent, MatcherAgent, TailorAgent

        total_fetched = 0
        total_new = 0
        total_matched = 0
        total_tailored = 0
        total_errors = 0

        try:
            threshold = int(os.getenv("MATCH_SCORE_THRESHOLD", "70"))
        except ValueError:
            threshold = 70

        self.progress.emit("Parsing candidate resume...")
        try:
            resume_schema, _from_cache = ResumeAgent().parse_resume()
        except Exception as e:
            self.progress.emit(f"Failed to parse resume: {e}")
            self.finished.emit(0, 0, 0, 0, 1)
            return

        sources = get_all_rss_sources()
        if not sources:
            self.progress.emit("No RSS sources configured. Please add one in Settings -> RSS Feeds.")
            self.finished.emit(0, 0, 0, 0, 0)
            return

        existing_jobs = get_all_jobs()
        existing_uids = {j.get("universal_id") for j in existing_jobs if j.get("universal_id")}
        existing_links = {j.get("job_link") for j in existing_jobs if j.get("job_link")}

        rss_agent = RSSJobAgent()
        matcher_agent = MatcherAgent()
        tailor_agent = TailorAgent()

        for source in sources:
            source_id = source["id"]
            source_name = source.get("name") or source.get("link")
            self.progress.emit(f"Fetching RSS feed: {source_name}...")

            try:
                feed = fetch_rss_feed(source["link"], max_items=20)
            except Exception as e:
                self.progress.emit(f"Error fetching '{source_name}': {e}")
                total_errors += 1
                continue

            items = feed.get("items", [])
            total_fetched += len(items)

            for item in items:
                link = item.get("link", "")
                uid = item.get("universal_id") or get_universal_id(link)

                if uid in existing_uids or link in existing_links:
                    continue

                existing_uids.add(uid)
                existing_links.add(link)

                self.progress.emit(f"Processing new job: {item.get('title') or link}")

                try:
                    structured = rss_agent.process_feed_item(item, rss_source_id=source_id)
                    job_id = create_job(
                        job_link=structured.canonical_url or link,
                        job_description=structured.clean_description or "",
                        status="pending",
                        job_match_score=0,
                        application_link=structured.application_url or link,
                        rss_source_id=source_id,
                        universal_id=uid,
                        structured_output=structured.to_json(),
                    )
                    total_new += 1
                except Exception as e:
                    self.progress.emit(f"Extraction failed for '{link}': {e}")
                    total_errors += 1
                    continue

                try:
                    match_result = matcher_agent.evaluate_match(
                        resume_data=resume_schema,
                        job_description=structured.clean_description or "",
                        job_title=structured.title,
                        company_name=structured.company,
                    )
                    update_job_match_score(job_id, match_result.match_score)
                    update_job_match_result(job_id, match_result.model_dump_json())
                    total_matched += 1
                except Exception as e:
                    self.progress.emit(f"ATS matching failed for job #{job_id}: {e}")
                    total_errors += 1
                    continue

                if match_result.match_score >= threshold:
                    try:
                        tailored = tailor_agent.tailor_resume(
                            resume_data=resume_schema,
                            job_description=structured.clean_description or "",
                            job_title=structured.title,
                            company_name=structured.company,
                        )
                        update_job_tailored_resume(job_id, tailored.model_dump_json())
                        total_tailored += 1
                    except Exception as e:
                        self.progress.emit(f"Resume tailoring failed for job #{job_id}: {e}")
                        total_errors += 1

        self.finished.emit(total_fetched, total_new, total_matched, total_tailored, total_errors)


class MainWindow(QMainWindow):
```

- [ ] **Step 4: Write and run the verification script**

This uses `unittest.mock.patch` to replace the four agent classes with fakes
(no real network/LLM calls) and runs `FindJobsWorker.run()` directly (not
`.start()`, so no real thread/event loop is needed). It creates one temporary
RSS source and cleans up every row it creates in `finally`, regardless of
outcome.

```bash
python -c "
import os
import json
from unittest.mock import patch

from database import init_db, create_rss_source, delete_rss_source, get_all_jobs, delete_job
init_db()


class FakeStructured:
    def __init__(self, title, company, link, desc):
        self.title = title
        self.company = company
        self.canonical_url = link
        self.application_url = link
        self.clean_description = desc

    def to_json(self):
        return json.dumps({'title': self.title, 'company': self.company})


class FakeRSSJobAgent:
    def process_feed_item(self, item, rss_source_id=None):
        return FakeStructured(item['title'], 'Acme', item['link'], item['summary'])


class FakeResumeAgent:
    def parse_resume(self):
        return ('FAKE_RESUME', True)


class FakeMatchResult:
    def __init__(self, score):
        self.match_score = score
        self.fit_level = 'Good Match'

    def model_dump_json(self):
        return json.dumps({'match_score': self.match_score, 'fit_level': self.fit_level})


class FakeMatcherAgent:
    def evaluate_match(self, resume_data, job_description, job_title=None, company_name=None):
        score = 90 if 'high' in job_description else 40
        return FakeMatchResult(score)


class FakeTailored:
    def model_dump_json(self):
        return json.dumps({'professional_summary': 'tailored'})


class FakeTailorAgent:
    def tailor_resume(self, resume_data, job_description, job_title=None, company_name=None):
        return FakeTailored()


FAKE_FEED = {
    'items': [
        {'title': 'High Fit Job', 'link': 'https://example.com/job/high', 'summary': 'high scoring description'},
        {'title': 'Low Fit Job', 'link': 'https://example.com/job/low', 'summary': 'low scoring description'},
    ]
}

source_id = create_rss_source(link='https://example.com/verify-task3-feed.xml', name='Verify Task3 Source')
progress_messages = []
results = {}

try:
    with patch('gui.main_window.fetch_rss_feed', return_value=FAKE_FEED), \\
         patch('agents.RSSJobAgent', FakeRSSJobAgent), \\
         patch('agents.ResumeAgent', FakeResumeAgent), \\
         patch('agents.MatcherAgent', FakeMatcherAgent), \\
         patch('agents.TailorAgent', FakeTailorAgent), \\
         patch.dict(os.environ, {'MATCH_SCORE_THRESHOLD': '70'}, clear=False):

        from gui.main_window import FindJobsWorker

        worker = FindJobsWorker()
        worker.progress.connect(lambda msg: progress_messages.append(msg))
        worker.finished.connect(lambda a, b, c, d, e: results.update(
            total_fetched=a, total_new=b, total_matched=c, total_tailored=d, total_errors=e
        ))
        worker.run()

    assert results == {'total_fetched': 2, 'total_new': 2, 'total_matched': 2, 'total_tailored': 1, 'total_errors': 0}, results

    jobs = [j for j in get_all_jobs() if j['rss_source_id'] == source_id]
    assert len(jobs) == 2, jobs

    high_job = next(j for j in jobs if j['job_link'].endswith('/high'))
    low_job = next(j for j in jobs if j['job_link'].endswith('/low'))

    assert high_job['job_match_score'] == 90, high_job
    assert high_job['tailored_resume'] is not None, high_job
    assert low_job['job_match_score'] == 40, low_job
    assert low_job['tailored_resume'] is None, low_job

    # Second run over the same feed must find zero new jobs (dedupe works).
    results.clear()
    with patch('gui.main_window.fetch_rss_feed', return_value=FAKE_FEED), \\
         patch('agents.RSSJobAgent', FakeRSSJobAgent), \\
         patch('agents.ResumeAgent', FakeResumeAgent), \\
         patch('agents.MatcherAgent', FakeMatcherAgent), \\
         patch('agents.TailorAgent', FakeTailorAgent), \\
         patch.dict(os.environ, {'MATCH_SCORE_THRESHOLD': '70'}, clear=False):
        worker2 = FindJobsWorker()
        worker2.finished.connect(lambda a, b, c, d, e: results.update(
            total_fetched=a, total_new=b, total_matched=c, total_tailored=d, total_errors=e
        ))
        worker2.run()
    assert results['total_new'] == 0, results

    print('ALL PASS')
finally:
    for j in get_all_jobs():
        if j['rss_source_id'] == source_id:
            delete_job(j['id'])
    delete_rss_source(source_id)
"
```

Expected output: `ALL PASS`.

Then verify `check_pipeline_prerequisites`:

```bash
python -c "
import os
from unittest.mock import patch
from gui.main_window import check_pipeline_prerequisites

with patch.dict(os.environ, {'OPENAI_API_KEY': 'your_openai_api_key_here'}, clear=False):
    msg = check_pipeline_prerequisites()
    assert msg and 'OPENAI_API_KEY' in msg, msg

with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-real-looking-key', 'RESUME_PATH': 'definitely_missing_resume.pdf'}, clear=False):
    msg = check_pipeline_prerequisites()
    assert msg and 'Resume file not found' in msg, msg

print('ALL PASS')
"
```

Expected output: `ALL PASS`.

- [ ] **Step 5: Commit**

```bash
git add gui/main_window.py .env.example
git commit -m "feat: add FindJobsWorker pipeline (fetch -> extract -> match -> tailor)"
```

---

### Task 4: GUI wiring — remove manual buttons, wire up Find Jobs, color-coded sorted table, AI report dialog

**Files:**
- Modify: `gui/main_window.py` (full-file rewrite — see rationale below)
- Test: ad-hoc script run via Bash

**Interfaces:**
- Consumes: `gui.main_window.FindJobsWorker`, `gui.main_window.check_pipeline_prerequisites`
  (Task 3); `database.update_job_match_result`/`update_job_tailored_resume` are read
  (not called) here — the worker already wrote them.
- Produces: `MainWindow` with a "Find Jobs" toolbar button/menu action, a 6-column
  jobs table (`ID, Job Link, Status, Match Score, Tailored, RSS Source`), and a
  "View AI Report" button. No other module imports `MainWindow`'s internals, so no
  downstream interface contract beyond what already exists (`run_app()`).

This task touches nearly every method in the file (removing 3 worker classes, 2
buttons, 5 handler methods, and restructuring table population), so instead of a
long sequence of small edits, this step replaces the entire file content — the
diff is large but every change traces directly to Task 3's additions plus the
design decisions in the spec (Tailored column, score color bands, default sort,
single read-only AI report button replacing Enrich/Match).

**Summary of what changes from the current file:**
- Removed: `RSSFetchWorker`, `EnrichJobWorker`, `MatchResumeWorker` classes;
  `enrich_btn`, `match_btn` and their handlers (`enrich_selected_job`,
  `on_enrich_finished`, `match_resume_for_selected_job`, `on_match_finished`,
  `_show_match_result_dialog`).
- Added: `QColor` import; jobs table gets a 6th column ("Tailored"); `load_jobs`
  now color-codes the Match Score cell by `matcher_agent`'s existing fit-level
  bands, sets numeric `EditRole` data on ID/Match Score for correct native
  sorting, sorts by score descending by default, and enables click-to-sort;
  `on_job_selected` enables `view_report_btn` only when `match_result` is present
  and renders the AI-extracted job details (unchanged from the prior session);
  new `view_ai_report` method + dialog reads `match_result`/`tailored_resume`
  straight from the DB (no LLM call) and renders both; toolbar button and File
  menu action renamed to "Find Jobs", calling `check_pipeline_prerequisites()`
  before starting `FindJobsWorker`.

- [ ] **Step 1: Replace the full file**

Write the complete new content to `gui/main_window.py`:

```python
"""
Main Application Window for RSS Job Hunter with Menu Bar (Settings -> RSS Feeds).
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextBrowser,
    QMessageBox,
    QStatusBar,
    QGroupBox,
    QListWidget,
    QInputDialog,
)

from database import (
    init_db,
    get_all_jobs,
    get_job,
    get_job_with_notes,
    create_job,
    add_note,
    get_all_rss_sources,
    update_job_match_score,
    update_job_match_result,
    update_job_tailored_resume,
)
from tools import fetch_rss_feed, get_universal_id
from gui.rss_feed_dialog import RSSFeedDialog


def check_pipeline_prerequisites() -> Optional[str]:
    """
    Validates required AI pipeline configuration before starting FindJobsWorker.

    Returns:
        An error message string if a prerequisite is missing, or None if all
        checks pass.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_openai_api_key_here":
        return (
            "OPENAI_API_KEY is not configured. Please set a valid OpenAI API key "
            "in your .env file before running Find Jobs."
        )

    resume_path = os.getenv("RESUME_PATH") or os.getenv("RESUME", "resume.pdf")
    if not Path(resume_path).exists():
        return (
            f"Resume file not found at '{resume_path}'. Please configure RESUME_PATH "
            "in your .env file and ensure the PDF exists before running Find Jobs."
        )

    return None


class FindJobsWorker(QThread):
    """
    Worker thread that fetches RSS feeds, deduplicates new items in code, extracts
    structured job data via the LLM rss_agent, scores each new job against the
    candidate's resume via the LLM matcher_agent, and generates a tailored resume
    via the LLM tailor_agent for any job whose score clears MATCH_SCORE_THRESHOLD.
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int, int, int, int)
    # total_fetched, total_new, total_matched, total_tailored, total_errors

    def run(self) -> None:
        from agents import RSSJobAgent, ResumeAgent, MatcherAgent, TailorAgent

        total_fetched = 0
        total_new = 0
        total_matched = 0
        total_tailored = 0
        total_errors = 0

        try:
            threshold = int(os.getenv("MATCH_SCORE_THRESHOLD", "70"))
        except ValueError:
            threshold = 70

        self.progress.emit("Parsing candidate resume...")
        try:
            resume_schema, _from_cache = ResumeAgent().parse_resume()
        except Exception as e:
            self.progress.emit(f"Failed to parse resume: {e}")
            self.finished.emit(0, 0, 0, 0, 1)
            return

        sources = get_all_rss_sources()
        if not sources:
            self.progress.emit("No RSS sources configured. Please add one in Settings -> RSS Feeds.")
            self.finished.emit(0, 0, 0, 0, 0)
            return

        existing_jobs = get_all_jobs()
        existing_uids = {j.get("universal_id") for j in existing_jobs if j.get("universal_id")}
        existing_links = {j.get("job_link") for j in existing_jobs if j.get("job_link")}

        rss_agent = RSSJobAgent()
        matcher_agent = MatcherAgent()
        tailor_agent = TailorAgent()

        for source in sources:
            source_id = source["id"]
            source_name = source.get("name") or source.get("link")
            self.progress.emit(f"Fetching RSS feed: {source_name}...")

            try:
                feed = fetch_rss_feed(source["link"], max_items=20)
            except Exception as e:
                self.progress.emit(f"Error fetching '{source_name}': {e}")
                total_errors += 1
                continue

            items = feed.get("items", [])
            total_fetched += len(items)

            for item in items:
                link = item.get("link", "")
                uid = item.get("universal_id") or get_universal_id(link)

                if uid in existing_uids or link in existing_links:
                    continue

                existing_uids.add(uid)
                existing_links.add(link)

                self.progress.emit(f"Processing new job: {item.get('title') or link}")

                try:
                    structured = rss_agent.process_feed_item(item, rss_source_id=source_id)
                    job_id = create_job(
                        job_link=structured.canonical_url or link,
                        job_description=structured.clean_description or "",
                        status="pending",
                        job_match_score=0,
                        application_link=structured.application_url or link,
                        rss_source_id=source_id,
                        universal_id=uid,
                        structured_output=structured.to_json(),
                    )
                    total_new += 1
                except Exception as e:
                    self.progress.emit(f"Extraction failed for '{link}': {e}")
                    total_errors += 1
                    continue

                try:
                    match_result = matcher_agent.evaluate_match(
                        resume_data=resume_schema,
                        job_description=structured.clean_description or "",
                        job_title=structured.title,
                        company_name=structured.company,
                    )
                    update_job_match_score(job_id, match_result.match_score)
                    update_job_match_result(job_id, match_result.model_dump_json())
                    total_matched += 1
                except Exception as e:
                    self.progress.emit(f"ATS matching failed for job #{job_id}: {e}")
                    total_errors += 1
                    continue

                if match_result.match_score >= threshold:
                    try:
                        tailored = tailor_agent.tailor_resume(
                            resume_data=resume_schema,
                            job_description=structured.clean_description or "",
                            job_title=structured.title,
                            company_name=structured.company,
                        )
                        update_job_tailored_resume(job_id, tailored.model_dump_json())
                        total_tailored += 1
                    except Exception as e:
                        self.progress.emit(f"Resume tailoring failed for job #{job_id}: {e}")
                        total_errors += 1

        self.finished.emit(total_fetched, total_new, total_matched, total_tailored, total_errors)


class MainWindow(QMainWindow):
    """Main Dashboard Window for RSS Job Hunter."""

    SCORE_COLORS = {
        "strong": QColor("#2e7d32"),   # 80-100
        "good": QColor("#f9a825"),     # 65-79
        "moderate": QColor("#ef6c00"), # 50-64
        "low": QColor("#c62828"),      # 0-49
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RSS Job Hunter - Dashboard")
        self.resize(1150, 700)

        # Initialize DB
        init_db()

        self._create_menu_bar()
        self._init_ui()
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Select Settings -> RSS Feeds to manage feeds.")

        self.load_jobs()

    def _create_menu_bar(self) -> None:
        """Creates top Menu Bar with File, Settings -> RSS Feeds, and Help menus."""
        menu_bar = self.menuBar()

        # --- FILE MENU ---
        file_menu = menu_bar.addMenu("&File")

        find_jobs_action = QAction("&Find Jobs", self)
        find_jobs_action.setShortcut("Ctrl+R")
        find_jobs_action.triggered.connect(self.find_jobs)
        file_menu.addAction(find_jobs_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- SETTINGS MENU ---
        settings_menu = menu_bar.addMenu("&Settings")

        rss_feeds_action = QAction("&RSS Feeds", self)
        rss_feeds_action.setShortcut("Ctrl+S")
        rss_feeds_action.setStatusTip("Open RSS Source Feed Management Settings")
        rss_feeds_action.triggered.connect(self.open_rss_settings_dialog)
        settings_menu.addAction(rss_feeds_action)

        # --- HELP MENU ---
        help_menu = menu_bar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def _init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)

        # Toolbar Control Bar
        toolbar_layout = QHBoxLayout()

        toolbar_layout.addWidget(QLabel("Filter Status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "pending", "applied", "interviewing", "rejected"])
        self.status_filter.currentTextChanged.connect(self.load_jobs)
        toolbar_layout.addWidget(self.status_filter)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search jobs by title, link, or description...")
        self.search_input.textChanged.connect(self.load_jobs)
        toolbar_layout.addWidget(self.search_input)

        self.find_jobs_btn = QPushButton("🔎 Find Jobs")
        self.find_jobs_btn.setStyleSheet("font-weight: bold; padding: 6px 12px; background-color: #2b5c8f;")
        self.find_jobs_btn.clicked.connect(self.find_jobs)
        toolbar_layout.addWidget(self.find_jobs_btn)

        self.settings_btn = QPushButton("⚙ Settings -> RSS Feeds")
        self.settings_btn.clicked.connect(self.open_rss_settings_dialog)
        toolbar_layout.addWidget(self.settings_btn)

        layout.addLayout(toolbar_layout)

        # Main Content Splitter (Left: Table, Right: Job Details)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Widget: Jobs Table
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.jobs_table = QTableWidget()
        self.jobs_table.setColumnCount(6)
        self.jobs_table.setHorizontalHeaderLabels(
            ["ID", "Job Link", "Status", "Match Score", "Tailored", "RSS Source"]
        )
        self.jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.itemSelectionChanged.connect(self.on_job_selected)

        left_layout.addWidget(self.jobs_table)
        splitter.addWidget(left_widget)

        # Right Widget: Job Details & Notes Pane
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Job Info Box
        job_info_group = QGroupBox("Job Details")
        job_info_layout = QVBoxLayout(job_info_group)

        self.job_title_label = QLabel("Select a job from the table to view details")
        self.job_title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.job_title_label.setWordWrap(True)
        job_info_layout.addWidget(self.job_title_label)

        self.job_desc_browser = QTextBrowser()
        self.job_desc_browser.setOpenExternalLinks(True)
        job_info_layout.addWidget(self.job_desc_browser)

        ai_actions_layout = QHBoxLayout()
        self.view_report_btn = QPushButton("📄 View AI Report")
        self.view_report_btn.setEnabled(False)
        self.view_report_btn.clicked.connect(self.view_ai_report)
        ai_actions_layout.addWidget(self.view_report_btn)
        ai_actions_layout.addStretch()
        job_info_layout.addLayout(ai_actions_layout)

        right_layout.addWidget(job_info_group)

        # Notes Group Box
        notes_group = QGroupBox("Job Notes")
        notes_layout = QVBoxLayout(notes_group)

        self.notes_list = QListWidget()
        notes_layout.addWidget(self.notes_list)

        notes_btn_layout = QHBoxLayout()
        self.add_note_btn = QPushButton("+ Add Note")
        self.add_note_btn.clicked.connect(self.add_note_to_selected_job)
        notes_btn_layout.addWidget(self.add_note_btn)
        notes_btn_layout.addStretch()

        notes_layout.addLayout(notes_btn_layout)
        right_layout.addWidget(notes_group)

        splitter.addWidget(right_widget)
        splitter.setSizes([650, 450])

        layout.addWidget(splitter)

    def open_rss_settings_dialog(self) -> None:
        """Opens Settings -> RSS Feeds dialog window."""
        dialog = RSSFeedDialog(self)
        dialog.exec()

    def _score_band_color(self, score: int) -> Optional[QColor]:
        """Returns the background color for a match score, matching matcher_agent's fit_level bands."""
        if score >= 80:
            return self.SCORE_COLORS["strong"]
        if score >= 65:
            return self.SCORE_COLORS["good"]
        if score >= 50:
            return self.SCORE_COLORS["moderate"]
        return self.SCORE_COLORS["low"]

    def load_jobs(self) -> None:
        """Loads jobs from database into the table widget, sorted by match score descending."""
        status = self.status_filter.currentText()
        search = self.search_input.text().strip()

        jobs = get_all_jobs(status_filter=status, search=search)
        jobs.sort(key=lambda j: (j.get("job_match_score") or 0), reverse=True)

        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.setRowCount(len(jobs))

        rss_sources = {s["id"]: s.get("name") or s.get("link") for s in get_all_rss_sources()}

        for row_idx, job in enumerate(jobs):
            score = job.get("job_match_score") or 0
            has_match = bool(job.get("match_result"))
            is_tailored = bool(job.get("tailored_resume"))

            id_item = QTableWidgetItem(str(job["id"]))
            id_item.setData(Qt.ItemDataRole.EditRole, job["id"])

            link_item = QTableWidgetItem(job.get("job_link") or "")
            status_item = QTableWidgetItem(job.get("status") or "pending")

            score_item = QTableWidgetItem(str(score))
            score_item.setData(Qt.ItemDataRole.EditRole, score)
            if has_match:
                color = self._score_band_color(score)
                score_item.setBackground(color)
                score_item.setForeground(QColor("#ffffff"))

            tailored_item = QTableWidgetItem("Yes" if is_tailored else "No")

            source_name = rss_sources.get(job.get("rss_source_id"), "Direct / Manual")
            source_item = QTableWidgetItem(source_name)

            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tailored_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.jobs_table.setItem(row_idx, 0, id_item)
            self.jobs_table.setItem(row_idx, 1, link_item)
            self.jobs_table.setItem(row_idx, 2, status_item)
            self.jobs_table.setItem(row_idx, 3, score_item)
            self.jobs_table.setItem(row_idx, 4, tailored_item)
            self.jobs_table.setItem(row_idx, 5, source_item)

        self.jobs_table.setSortingEnabled(True)
        self.status_bar.showMessage(f"Loaded {len(jobs)} job records.")

    def on_job_selected(self) -> None:
        """Displays selected job details and notes on the right pane."""
        selected_ranges = self.jobs_table.selectedRanges()
        if not selected_ranges:
            self.job_title_label.setText("Select a job from the table to view details")
            self.job_desc_browser.clear()
            self.notes_list.clear()
            self.view_report_btn.setEnabled(False)
            return

        row = selected_ranges[0].topRow()
        job_id = int(self.jobs_table.item(row, 0).text())

        job = get_job_with_notes(job_id)
        if not job:
            return

        link = job.get("job_link", "")
        self.job_title_label.setText(f"Job #{job['id']}: {link}")

        source_info = job.get("rss_source", {})
        source_str = source_info.get("name") if source_info else "N/A"

        structured_html = ""
        structured_raw = job.get("structured_output")
        if structured_raw:
            try:
                structured = json.loads(structured_raw)
                skills = ", ".join(structured.get("required_skills") or [])
                structured_html = (
                    f"<h3>AI-Extracted Details</h3>"
                    f"<b>Title:</b> {structured.get('title', 'N/A')}<br>"
                    f"<b>Company:</b> {structured.get('company', 'N/A')}<br>"
                    f"<b>Location:</b> {structured.get('location', 'N/A')} "
                    f"({'Remote' if structured.get('is_remote') else 'On-site'})<br>"
                    f"<b>Employment Type:</b> {structured.get('employment_type', 'N/A')}<br>"
                    f"<b>Experience Level:</b> {structured.get('experience_level', 'N/A')}<br>"
                    f"<b>Salary:</b> {structured.get('salary_range') or 'N/A'}<br>"
                    f"<b>Skills:</b> {skills or 'N/A'}<br>"
                    f"<p><i>{structured.get('job_summary') or ''}</i></p>"
                    f"<hr>"
                )
            except Exception:
                structured_html = ""

        content_html = (
            f"<b>Job Link:</b> <a href='{link}'>{link}</a><br>"
            f"<b>Status:</b> {job.get('status')}<br>"
            f"<b>Match Score:</b> {job.get('job_match_score')}<br>"
            f"<b>RSS Source:</b> {source_str}<br>"
            f"<hr>"
            f"{structured_html}"
            f"<h3>Job Description</h3>"
            f"<p>{job.get('job_description') or 'No description text provided.'}</p>"
        )
        self.job_desc_browser.setHtml(content_html)
        self.view_report_btn.setEnabled(bool(job.get("match_result")))

        # Populate Notes List
        self.notes_list.clear()
        notes = job.get("notes", [])
        for note in notes:
            self.notes_list.addItem(f"[{note.get('created_at', '')}] {note.get('note')}")

    def add_note_to_selected_job(self) -> None:
        """Adds a new note to the selected job."""
        selected_ranges = self.jobs_table.selectedRanges()
        if not selected_ranges:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        row = selected_ranges[0].topRow()
        job_id = int(self.jobs_table.item(row, 0).text())

        text, ok = QInputDialog.getText(self, "Add Job Note", "Enter note text:")
        if ok and text.strip():
            add_note(job_id, text.strip())
            self.on_job_selected()
            self.status_bar.showMessage("Note added successfully.")

    def _get_selected_job_id(self) -> Optional[int]:
        """Returns the ID of the currently selected job in the table, or None."""
        selected_ranges = self.jobs_table.selectedRanges()
        if not selected_ranges:
            return None
        row = selected_ranges[0].topRow()
        item = self.jobs_table.item(row, 0)
        return int(item.text()) if item else None

    def view_ai_report(self) -> None:
        """Displays the stored ATS match breakdown and tailored resume for the selected job, read-only."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        job = get_job(job_id)
        if not job or not job.get("match_result"):
            QMessageBox.information(
                self, "No AI Report", "This job has not been analyzed yet. Run Find Jobs to process it."
            )
            return

        match_result = json.loads(job["match_result"])
        tailored_resume = json.loads(job["tailored_resume"]) if job.get("tailored_resume") else None

        dialog = QDialog(self)
        dialog.setWindowTitle(f"AI Report: {match_result.get('match_score')}/100 ({match_result.get('fit_level')})")
        dialog.setMinimumSize(650, 550)
        layout = QVBoxLayout(dialog)

        def _list_html(items: List[str]) -> str:
            return "".join(f"<li>{i}</li>" for i in items) or "<li>None</li>"

        exp_eval = match_result.get("experience_evaluation") or {}
        html = (
            f"<h2>{match_result.get('match_score')}/100 &mdash; {match_result.get('fit_level')}</h2>"
            f"<p>{match_result.get('executive_summary', '')}</p>"
            f"<h3>Matching Skills</h3><ul>{_list_html(match_result.get('matching_skills', []))}</ul>"
            f"<h3>Missing Skills</h3><ul>{_list_html(match_result.get('missing_skills', []))}</ul>"
            f"<h3>Key Strengths</h3><ul>{_list_html(match_result.get('key_strengths', []))}</ul>"
            f"<h3>Gap Areas</h3><ul>{_list_html(match_result.get('gap_areas', []))}</ul>"
            f"<h3>Tailoring Recommendations</h3><ul>{_list_html(match_result.get('tailoring_recommendations', []))}</ul>"
            f"<h3>Experience Evaluation</h3><p>{exp_eval.get('commentary', '')}</p>"
        )

        if tailored_resume:
            skills = tailored_resume.get("skills") or {}
            work_exp_html = ""
            for exp in tailored_resume.get("work_experience", []):
                bullets = "".join(f"<li>{r}</li>" for r in exp.get("responsibilities", []))
                work_exp_html += (
                    f"<h4>{exp.get('job_title', '')} &mdash; {exp.get('company', '')}</h4>"
                    f"<ul>{bullets}</ul>"
                )
            html += (
                f"<hr><h2>Tailored Resume</h2>"
                f"<p><i>{tailored_resume.get('professional_summary', '')}</i></p>"
                f"<h3>Emphasized Skills</h3>"
                f"<p><b>Technical:</b> {', '.join(skills.get('technical_skills', []))}</p>"
                f"<h3>Work Experience</h3>{work_exp_html}"
            )

        browser = QTextBrowser()
        browser.setHtml(html)
        layout.addWidget(browser)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def find_jobs(self) -> None:
        """Validates AI pipeline prerequisites, then starts the background Find Jobs worker."""
        error = check_pipeline_prerequisites()
        if error:
            QMessageBox.critical(self, "Cannot Run Find Jobs", error)
            return

        self.find_jobs_btn.setEnabled(False)
        self.status_bar.showMessage("Finding jobs: fetching, extracting, matching, and tailoring in background...")

        self.worker = FindJobsWorker()
        self.worker.progress.connect(self.status_bar.showMessage)
        self.worker.finished.connect(self.on_find_jobs_finished)
        self.worker.start()

    def on_find_jobs_finished(
        self, total_fetched: int, total_new: int, total_matched: int, total_tailored: int, total_errors: int
    ) -> None:
        """Handles background Find Jobs pipeline completion."""
        self.find_jobs_btn.setEnabled(True)
        self.load_jobs()
        msg = (
            f"Find Jobs complete! Fetched: {total_fetched}, New: {total_new}, "
            f"Matched: {total_matched}, Tailored: {total_tailored}, Errors: {total_errors}."
        )
        self.status_bar.showMessage(msg)
        QMessageBox.information(self, "Find Jobs Complete", msg)

    def show_about_dialog(self) -> None:
        """Shows About dialog."""
        QMessageBox.about(
            self,
            "About RSS Job Hunter",
            "<h3>RSS Job Hunter</h3>"
            "<p>An AI-powered Job Hunter & RSS Feed Aggregator.</p>"
            "<p>Menu Bar: <b>Settings -> RSS Feeds</b> to manage feed URLs.</p>",
        )


def run_app():
    """Runs the PyQt6 GUI Application."""
    app = QApplication(sys.argv)

    # Apply modern dark theme if available
    try:
        import qdarktheme
        qdarktheme.setup_theme("dark")
    except Exception:
        pass

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    run_app()
```

- [ ] **Step 2: Byte-compile check**

```bash
python -m py_compile gui/main_window.py && echo COMPILE_OK
```

Expected output: `COMPILE_OK`.

- [ ] **Step 3: Write and run the offscreen GUI smoke test**

Seeds jobs across all four score bands plus one unanalyzed job, constructs the
full `MainWindow` offscreen, and verifies color coding, the Tailored column,
default sort order, and the View AI Report button's enable state. Cleans up
every row it creates in `finally`.

```bash
QT_QPA_PLATFORM=offscreen python -c "
import sys, json
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor
from gui.main_window import MainWindow
from database import init_db, create_job, update_job_match_score, update_job_match_result, update_job_tailored_resume, delete_job

init_db()

def make_job(score, tailored, link_suffix):
    job_id = create_job(job_link=f'https://example.com/verify-task4-{link_suffix}', job_description='desc', status='pending')
    update_job_match_score(job_id, score)
    update_job_match_result(job_id, json.dumps({'match_score': score, 'fit_level': 'x'}))
    if tailored:
        update_job_tailored_resume(job_id, json.dumps({'professional_summary': 'tailored'}))
    return job_id

job_ids = []
try:
    job_ids.append(make_job(90, True, 'strong'))   # green, tailored
    job_ids.append(make_job(70, False, 'good'))    # amber
    job_ids.append(make_job(55, False, 'moderate'))# orange
    job_ids.append(make_job(20, False, 'low'))     # red
    unanalyzed_id = create_job(job_link='https://example.com/verify-task4-unanalyzed', job_description='desc', status='pending')
    job_ids.append(unanalyzed_id)

    app = QApplication(sys.argv)
    w = MainWindow()
    w.load_jobs()

    assert w.jobs_table.columnCount() == 6
    headers = [w.jobs_table.horizontalHeaderItem(i).text() for i in range(6)]
    assert headers == ['ID', 'Job Link', 'Status', 'Match Score', 'Tailored', 'RSS Source'], headers

    # Row 0 must be the highest score (90) given default sort-by-score-desc.
    assert w.jobs_table.item(0, 3).text() == '90', w.jobs_table.item(0, 3).text()
    row0_link = w.jobs_table.item(0, 1).text()
    assert row0_link.endswith('strong'), row0_link
    assert w.jobs_table.item(0, 4).text() == 'Yes'
    assert w.jobs_table.item(0, 3).background().color() == QColor('#2e7d32')

    # Find and check the unanalyzed row: no color, Tailored = No.
    unanalyzed_row = next(r for r in range(w.jobs_table.rowCount()) if w.jobs_table.item(r, 1).text().endswith('unanalyzed'))
    assert w.jobs_table.item(unanalyzed_row, 4).text() == 'No'
    default_bg = w.jobs_table.item(unanalyzed_row, 3).background().color()
    assert default_bg != QColor('#2e7d32') and default_bg != QColor('#c62828'), default_bg

    w.jobs_table.selectRow(0)
    assert w.view_report_btn.isEnabled() is True

    w.jobs_table.selectRow(unanalyzed_row)
    assert w.view_report_btn.isEnabled() is False

    print('ALL PASS')
finally:
    for jid in job_ids:
        delete_job(jid)
"
```

Expected output: `ALL PASS`.

- [ ] **Step 4: Manual run-through**

Launch the real app and confirm visually (requires a display, not offscreen):

```bash
python main.py
```

Check: "Find Jobs" appears on the toolbar and in the File menu (Ctrl+R still
works); with no `OPENAI_API_KEY`/resume configured, clicking it shows the
blocking prerequisite error and does not spin up a background thread. Close
the app when done (`Ctrl+Q` or window close).

- [ ] **Step 5: Commit**

```bash
git add gui/main_window.py
git commit -m "feat: wire Find Jobs pipeline into GUI with color-coded, score-sorted jobs table"
```
