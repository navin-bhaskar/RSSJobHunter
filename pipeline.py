"""
Core Find Jobs pipeline: fetch RSS feeds, deduplicate new items, extract structured
job data via the LLM rss_agent, score each new job against the candidate's resume via
the LLM matcher_agent, and generate a tailored resume via the LLM tailor_agent for any
job whose score clears MATCH_SCORE_THRESHOLD.

Shared by the GUI's FindJobsWorker (gui/main_window.py) and the headless runner
(runner.py) so both drive the exact same pipeline logic.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from database import (
    get_all_jobs,
    get_all_rss_sources,
    create_job,
    delete_job,
    update_job_match_score,
    update_job_match_result,
    update_job_tailored_resume,
)
from tools import fetch_rss_feed, get_universal_id
from agent_tools import (
    is_mqtt_configured,
    is_pushover_configured,
    send_mqtt_notification,
    send_pushover_notification,
)


class PipelineCancelled(Exception):
    """Raised internally to unwind run_find_jobs_pipeline() once cancellation is requested."""


@dataclass
class PipelineResult:
    total_fetched: int = 0
    total_new: int = 0
    total_matched: int = 0
    total_tailored: int = 0
    total_errors: int = 0
    cancelled: bool = False


def check_llm_prerequisites() -> Optional[str]:
    """
    Validates the API key and resume file required by any LLM-backed action
    (Find Jobs, Tailor Resume).

    Returns:
        An error message string if a prerequisite is missing, or None if all
        checks pass.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_openai_api_key_here":
        return (
            "OPENAI_API_KEY is not configured. Please set a valid OpenAI API key "
            "in your .env file."
        )

    resume_path = os.getenv("RESUME_PATH") or os.getenv("RESUME", "resume.pdf")
    if not Path(resume_path).exists():
        return (
            f"Resume file not found at '{resume_path}'. Please configure RESUME_PATH "
            "in your .env file and ensure the PDF exists."
        )

    return None


def check_pipeline_prerequisites() -> Optional[str]:
    """
    Validates required AI pipeline configuration before starting run_find_jobs_pipeline
    (LLM prerequisites plus at least one configured RSS feed).

    Returns:
        An error message string if a prerequisite is missing, or None if all
        checks pass.
    """
    error = check_llm_prerequisites()
    if error:
        return error

    if not get_all_rss_sources():
        return (
            "No RSS feeds are configured. Please add at least one feed in "
            "Settings -> RSS Feeds before running Find Jobs."
        )

    return None


def run_find_jobs_pipeline(
    progress_callback: Optional[Callable[[str], None]] = None,
    on_job_saved: Optional[Callable[[], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> PipelineResult:
    """
    Runs one full Find Jobs pass: fetch -> extract -> match -> tailor.

    Args:
        progress_callback: called with a human-readable status string as the pipeline
            progresses (e.g. to update a GUI status bar or write to a log).
        on_job_saved: called whenever a job row is created or updated, so a caller can
            refresh a live view of the jobs table.
        should_cancel: polled between feeds, between items, and immediately after a job
            row is created; if it returns True, the run unwinds via PipelineCancelled.
            A job that was created but not yet scored is rolled back (deleted) on
            cancellation; a job that has already been scored is kept.

    Returns:
        A PipelineResult summarizing the run.
    """
    from agents import RSSJobAgent, ResumeAgent, MatcherAgent, TailorAgent

    def _progress(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    def _job_saved() -> None:
        if on_job_saved:
            on_job_saved()

    def _check_cancel() -> None:
        if should_cancel and should_cancel():
            raise PipelineCancelled()

    result = PipelineResult()
    pending_job_id: Optional[int] = None

    try:
        threshold = int(os.getenv("MATCH_SCORE_THRESHOLD", "70"))
    except ValueError:
        threshold = 70

    _progress("Parsing candidate resume...")
    try:
        resume_schema, _from_cache = ResumeAgent().parse_resume()
    except Exception as e:
        _progress(f"Failed to parse resume: {e}")
        result.total_errors = 1
        return result

    sources = get_all_rss_sources()
    if not sources:
        _progress("No RSS sources configured. Please add one in Settings -> RSS Feeds.")
        return result

    existing_jobs = get_all_jobs(include_deleted=True)
    existing_uids = {j.get("universal_id") for j in existing_jobs if j.get("universal_id")}
    existing_links = {j.get("job_link") for j in existing_jobs if j.get("job_link")}

    rss_agent = RSSJobAgent()
    matcher_agent = MatcherAgent()
    tailor_agent = TailorAgent()

    try:
        for source in sources:
            _check_cancel()

            source_id = source["id"]
            source_name = source.get("name") or source.get("link")
            _progress(f"Fetching RSS feed: {source_name}...")

            try:
                feed = fetch_rss_feed(source["link"], max_items=20)
            except Exception as e:
                _progress(f"Error fetching '{source_name}': {e}")
                result.total_errors += 1
                continue

            items = feed.get("items", [])
            result.total_fetched += len(items)

            for item in items:
                _check_cancel()

                link = item.get("link", "")
                uid = item.get("universal_id") or get_universal_id(link)

                if uid in existing_uids or link in existing_links:
                    continue

                existing_uids.add(uid)
                existing_links.add(link)

                _progress(f"Processing new job: {item.get('title') or link}")

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
                    result.total_new += 1
                    pending_job_id = job_id
                    _job_saved()
                except Exception as e:
                    _progress(f"Extraction failed for '{link}': {e}")
                    result.total_errors += 1
                    continue

                _check_cancel()

                try:
                    match_result = matcher_agent.evaluate_match(
                        resume_data=resume_schema,
                        job_description=structured.clean_description or "",
                        job_title=structured.title,
                        company_name=structured.company,
                    )
                    update_job_match_score(job_id, match_result.match_score)
                    update_job_match_result(job_id, match_result.model_dump_json())
                    result.total_matched += 1
                    pending_job_id = None
                    _progress(f"Matched '{structured.title}' — score: {match_result.match_score}")
                    _job_saved()
                except Exception as e:
                    _progress(f"ATS matching failed for job #{job_id}: {e}")
                    result.total_errors += 1
                    continue

                if match_result.match_score >= threshold:
                    _check_cancel()

                    job_url = structured.application_url or structured.canonical_url or link

                    if is_pushover_configured():
                        sent = send_pushover_notification(
                            message=f"{structured.company or 'Unknown company'} — score {match_result.match_score}/100",
                            title=f"New job match: {structured.title or 'Untitled'}",
                            url=job_url,
                            url_title="View job",
                        )
                        if sent:
                            _progress(f"Sent Pushover notification for '{structured.title}'")

                    if is_mqtt_configured():
                        sent = send_mqtt_notification(
                            message=f"{structured.company or 'Unknown company'} — score {match_result.match_score}/100",
                            title=f"New job match: {structured.title or 'Untitled'}",
                            url=job_url,
                            score=match_result.match_score,
                        )
                        if sent:
                            _progress(f"Sent MQTT notification for '{structured.title}'")

                    try:
                        tailored = tailor_agent.tailor_resume(
                            resume_data=resume_schema,
                            job_description=structured.clean_description or "",
                            job_title=structured.title,
                            company_name=structured.company,
                        )
                        update_job_tailored_resume(job_id, tailored.model_dump_json())
                        result.total_tailored += 1
                        _progress(f"Tailored resume for '{structured.title}'")
                        _job_saved()
                    except Exception as e:
                        _progress(f"Resume tailoring failed for job #{job_id}: {e}")
                        result.total_errors += 1
    except PipelineCancelled:
        result.cancelled = True
        if pending_job_id is not None:
            try:
                delete_job(pending_job_id)
                result.total_new -= 1
            except Exception as e:
                _progress(f"Failed to roll back incomplete job #{pending_job_id}: {e}")
            pending_job_id = None
        _progress("Cancelled by user.")
        _job_saved()

    return result
