# Automated Job Pipeline: Fetch → Extract → Match → Tailor

Date: 2026-09-07
Status: Approved for implementation

## Problem

Today, RSS fetching stores raw job postings only. Structured extraction (`rss_agent`),
ATS matching (`resume_agent` + `matcher_agent`), and resume tailoring are either
missing (tailoring) or require manual, one-job-at-a-time button clicks (`Enrich with AI`,
`Match Resume` from the prior session). The user wants a single action that runs the
whole pipeline automatically for every newly discovered job, with high-fit jobs
visually surfaced.

## Goals

- One "Find Jobs" action runs the full pipeline: fetch → dedupe → extract → match →
  conditionally tailor.
- No wasted LLM calls: dedupe happens in code (SHA-256 `universal_id`) before any
  extraction call; tailoring only runs when the ATS score clears a configurable
  threshold.
- Missing configuration (API key, resume file) fails loudly and blocks the pipeline
  before any work starts, rather than partial/silent failure.
- A single bad job (LLM error, malformed output) does not abort the whole run.
- Match scores are visible at a glance (color-coded) and the table defaults to
  highest-score-first.
- A fully automatic pipeline replaces the manual per-job Enrich/Match buttons added
  in the previous session — those are removed.

## Non-goals

- Bounded/parallel LLM concurrency (sequential processing is acceptable at current
  RSS volumes — capped at 20 items/source).
- A GUI settings screen for the match threshold (env var is sufficient for now).
- Manual per-job re-run controls (re-running means clicking "Find Jobs" again, which
  is a no-op for jobs already in the DB since dedupe skips them — re-analysis of an
  existing job is out of scope for this pass).

## New agent: `agents/tailor_agent/`

Mirrors the existing `resume_agent`/`matcher_agent` package structure.

- `agent.py`: `TailorAgent.tailor_resume(resume_data: ATSResumeSchema, job_description: str, job_title: Optional[str] = None, company_name: Optional[str] = None) -> ATSResumeSchema`.
  Reuses `resume_agent.schema.ATSResumeSchema` as both input and output type — no new
  schema file. System prompt instructs the LLM to rewrite `professional_summary` and
  `work_experience[].responsibilities` to emphasize the target job's keywords/requirements,
  and reorder/prioritize `skills` toward what the posting asks for, while explicitly
  forbidding fabrication: names, companies, dates, degrees, and titles must stay
  factually unchanged from the source resume.
- `__init__.py`: exports `TailorAgent`, `tailor_resume_for_job()` convenience function.
  Registered in `agents/__init__.py`.
- `if __name__ == "__main__"` CLI entrypoint for standalone testing, consistent with
  its sibling agents.

## Data model changes

`database/schema.sql`:
- `jobs.match_result TEXT` — full `ATSMatchResult` JSON (skills, gaps, recommendations,
  experience evaluation).
- `jobs.tailored_resume TEXT` — full tailored `ATSResumeSchema` JSON, populated only
  when `job_match_score >= MATCH_SCORE_THRESHOLD`.

`database/database.py`:
- Migration checks for both new columns, following the existing pattern used for
  `structured_output`.
- New CRUD functions: `update_job_match_result(job_id, match_result_json) -> bool`,
  `update_job_tailored_resume(job_id, tailored_resume_json) -> bool`.
- Both exported from `database/__init__.py`.

`.env.example`:
- New `MATCH_SCORE_THRESHOLD=70` with an explanatory comment. Matches the "Good
  Match" boundary already defined in `matcher_agent`'s scoring rubric.

## Pipeline worker: `FindJobsWorker` (GUI, replaces `RSSFetchWorker`)

Also replaces/removes `EnrichJobWorker` and `MatchResumeWorker` and their buttons
from the previous session — this worker subsumes both.

**Preflight (main thread, before the worker starts):**
- `OPENAI_API_KEY` must be set and not the placeholder value.
- The resume file (`RESUME_PATH`/`RESUME` env var, default `resume.pdf`) must exist.
- Either failing shows a blocking `QMessageBox.critical` explaining what to fix; the
  worker thread is never started.

**Worker `run()`:**
1. Parse the candidate resume once via `ResumeAgent().parse_resume()` (SHA-256 file
   hash cache — real LLM call only on first run or after the resume file changes).
2. For each configured RSS source, fetch raw items via the existing
   `tools.fetch_rss_feed`.
3. For each raw item, compute `universal_id` in code (existing `get_universal_id`
   SHA-256 helper) and skip if a job with that `universal_id` or `job_link` already
   exists — no LLM call for already-known jobs.
4. For each genuinely new item:
   a. Call `RSSJobAgent.process_feed_item(...)` to get a `StructuredJobListingSchema`.
      `create_job(...)` immediately with `job_link`, `job_description` (clean
      description), `application_link`, `rss_source_id`, `universal_id`,
      `structured_output` (JSON). Stored even if later steps fail, so raw+structured
      data is never lost to a downstream error.
   b. Call `MatcherAgent().evaluate_match(resume_data=<parsed resume>,
      job_description=<clean_description>, job_title=..., company_name=...)`.
      Save `job_match_score` (int) and `match_result` (full JSON) via the new update
      functions.
   c. If `job_match_score >= MATCH_SCORE_THRESHOLD`, call
      `TailorAgent().tailor_resume(...)` and save `tailored_resume` JSON.
   d. Any exception in (a)/(b)/(c) for this specific item is caught, emitted as a
      progress message, and the loop continues to the next item. This is distinct
      from the preflight check: only missing global config is a hard stop; a single
      bad job is a skip-and-continue.
5. Emits a `finished(total_fetched, total_new, total_matched, total_tailored,
   total_errors)` signal.

## GUI changes (`gui/main_window.py`)

- Remove `RSSFetchWorker`, `EnrichJobWorker`, `MatchResumeWorker`, the `enrich_btn`,
  `match_btn`, and their handler methods (`enrich_selected_job`,
  `on_enrich_finished`, `match_resume_for_selected_job`, `on_match_finished`,
  `_show_match_result_dialog`'s match-only version) from the previous session.
- Add `FindJobsWorker` as described above.
- Toolbar button and `File` menu action renamed to **"🔎 Find Jobs"** (keeps the
  `Ctrl+R` shortcut and background-thread-with-progress UX of the old fetch action).
- Jobs table:
  - New **Tailored** column (Yes/No), reflecting whether `tailored_resume` is
    populated for that job.
  - **Match Score** cell background color-coded using `matcher_agent`'s existing
    `fit_level` bands: Strong Match (80-100) green, Good Match (65-79) amber,
    Moderate Match (50-64) orange, Low Match (0-49) red/gray. A job with no
    `match_result` yet (score 0, unanalyzed) stays uncolored/default.
  - ID and Match Score cells carry numeric `EditRole` data (not just display text) so
    Qt's native column sorting compares numerically, not lexically.
  - `setSortingEnabled(True)` for click-to-sort on any column; initial/default sort
    is by Match Score descending (highest fit first).
- Job details pane: keeps the existing structured-job-info rendering (already built)
  and match score/fit level. Replace the old separate match-result dialog with a
  single **"📄 View AI Report"** button — enabled only when `match_result` is present
  for the selected job — that reads `match_result` and `tailored_resume` straight
  from the DB and renders both (match breakdown; tailored resume summary/skills/
  work-experience bullets if present). No LLM call from this button.

## Error handling summary

| Failure | Behavior |
|---|---|
| Missing/placeholder `OPENAI_API_KEY` | Blocking dialog before worker starts; pipeline does not run. |
| Missing resume file | Blocking dialog before worker starts; pipeline does not run. |
| `rss_agent` extraction fails for one item | Logged to status bar, item skipped, loop continues. |
| `matcher_agent` fails for one item (already stored via `create_job`) | Logged, job keeps `structured_output` but `job_match_score`/`match_result` stay unset; loop continues. |
| `tailor_agent` fails for one item that cleared the threshold | Logged, job keeps its match data but `tailored_resume` stays unset; loop continues. |
| RSS source itself unreachable | Logged (existing behavior), other sources still processed. |

## Testing approach

- Byte-compile / import-smoke-test all modified and new Python modules.
- Offscreen (`QT_QPA_PLATFORM=offscreen`) construction test of `MainWindow` to catch
  layout/wiring errors without a display.
- Seed the DB directly (bypassing the LLM) with jobs across all four score bands plus
  one unanalyzed job, and verify: color coding, Tailored column, and default
  sort order render correctly.
- Unit-style exercise of `TailorAgent`'s schema validation path (mocking the Agents
  SDK `Runner.run_sync` call) is out of scope for this pass unless a test harness
  already exists for the sibling agents (none currently does — they're smoke-tested
  via `__main__`, not unit tests). Follow that existing convention rather than
  introducing a new one.
