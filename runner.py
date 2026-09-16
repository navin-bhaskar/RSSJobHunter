"""
Headless runner mode: polls RSS feeds on a fixed interval and runs the Find Jobs
pipeline (fetch -> extract -> match -> tailor) with no GUI.

Usage:
    python main.py --runner

Configure the poll interval via the RUNNER_INTERVAL_MINUTES environment variable
(defaults to 30). Progress and per-cycle summaries are logged to both stdout and
logs/runner.log. Exit with Ctrl+C.
"""

import logging
import time
from pathlib import Path

from database import init_db
from pipeline import check_pipeline_prerequisites, run_find_jobs_pipeline

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "runner.log"
DEFAULT_INTERVAL_MINUTES = 30

logger = logging.getLogger("runner")


def _configure_logging() -> None:
    """Configures the root logger with console + file handlers so runner.py's own
    log calls and the existing per-module loggers (rss_agent, job_extractor,
    rss_reader, ...) are all captured."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def _get_interval_minutes() -> int:
    import os

    try:
        return int(os.getenv("RUNNER_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES)))
    except ValueError:
        return DEFAULT_INTERVAL_MINUTES


def _run_cycle() -> None:
    error = check_pipeline_prerequisites()
    if error:
        logger.error("Cannot run Find Jobs pipeline: %s", error)
        return

    result = run_find_jobs_pipeline(progress_callback=logger.info)
    logger.info(
        "Cycle complete. Fetched: %d, New: %d, Matched: %d, Tailored: %d, Errors: %d.",
        result.total_fetched,
        result.total_new,
        result.total_matched,
        result.total_tailored,
        result.total_errors,
    )


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    init_db()
    _configure_logging()

    interval_minutes = _get_interval_minutes()
    logger.info("Starting RSS Job Hunter runner (interval: %d minutes). Press Ctrl+C to stop.", interval_minutes)

    try:
        while True:
            try:
                _run_cycle()
            except Exception:
                logger.exception("Unhandled error during Find Jobs cycle; will retry next interval.")

            logger.info("Sleeping for %d minutes until next cycle...", interval_minutes)
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        logger.info("Runner stopped.")


if __name__ == "__main__":
    main()
