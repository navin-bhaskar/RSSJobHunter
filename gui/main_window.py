"""
Main Application Window for RSS Job Hunter with Menu Bar (Settings -> RSS Feeds).
"""

import html
import json
import os
import sys
from pathlib import Path
from typing import Optional, List
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication,
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
from dotenv import load_dotenv

load_dotenv()


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
        "strong": (QColor("#2e7d32"), QColor("#ffffff")),    # 80-100, white on green
        "good": (QColor("#f9a825"), QColor("#1a1a1a")),       # 65-79, dark text on amber (contrast fix)
        "moderate": (QColor("#ef6c00"), QColor("#ffffff")),   # 50-64, white on orange
        "low": (QColor("#c62828"), QColor("#ffffff")),        # 0-49, white on red
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RSS Job Hunter - Dashboard")
        self.resize(1150, 700)

        self._run_log: List[str] = []
        self._initial_sort_applied = False

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

        self.find_jobs_action = QAction("&Find Jobs", self)
        self.find_jobs_action.setShortcut("Ctrl+R")
        self.find_jobs_action.triggered.connect(self.find_jobs)
        file_menu.addAction(self.find_jobs_action)

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

    def _score_band_colors(self, score: int) -> tuple[QColor, QColor]:
        """Returns (background, foreground) colors for a match score, matching matcher_agent's fit_level bands."""
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
                bg, fg = self._score_band_colors(score)
                score_item.setBackground(bg)
                score_item.setForeground(fg)

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
        if not self._initial_sort_applied:
            self.jobs_table.sortItems(3, Qt.SortOrder.DescendingOrder)
            self._initial_sort_applied = True
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
                    f"<b>Title:</b> {html.escape(str(structured.get('title', 'N/A')))}<br>"
                    f"<b>Company:</b> {html.escape(str(structured.get('company', 'N/A')))}<br>"
                    f"<b>Location:</b> {html.escape(str(structured.get('location', 'N/A')))} "
                    f"({'Remote' if structured.get('is_remote') else 'On-site'})<br>"
                    f"<b>Employment Type:</b> {html.escape(str(structured.get('employment_type', 'N/A')))}<br>"
                    f"<b>Experience Level:</b> {html.escape(str(structured.get('experience_level', 'N/A')))}<br>"
                    f"<b>Salary:</b> {html.escape(str(structured.get('salary_range') or 'N/A'))}<br>"
                    f"<b>Skills:</b> {html.escape(skills) if skills else 'N/A'}<br>"
                    f"<p><i>{html.escape(str(structured.get('job_summary') or ''))}</i></p>"
                    f"<hr>"
                )
            except Exception:
                structured_html = ""

        content_html = (
            f"<b>Job Link:</b> <a href='{link}'>{link}</a><br>"
            f"<b>Status:</b> {job.get('status')}<br>"
            f"<b>Match Score:</b> {job.get('job_match_score')}<br>"
            f"<b>RSS Source:</b> {html.escape(str(source_str)) if source_str else 'N/A'}<br>"
            f"<hr>"
            f"{structured_html}"
            f"<h3>Job Description</h3>"
            f"<p>{html.escape(job.get('job_description') or 'No description text provided.')}</p>"
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
            return "".join(f"<li>{html.escape(str(i))}</li>" for i in items) or "<li>None</li>"

        exp_eval = match_result.get("experience_evaluation") or {}
        report_html = (
            f"<h2>{match_result.get('match_score')}/100 &mdash; {html.escape(str(match_result.get('fit_level') or ''))}</h2>"
            f"<p>{html.escape(str(match_result.get('executive_summary') or ''))}</p>"
            f"<h3>Matching Skills</h3><ul>{_list_html(match_result.get('matching_skills', []))}</ul>"
            f"<h3>Missing Skills</h3><ul>{_list_html(match_result.get('missing_skills', []))}</ul>"
            f"<h3>Key Strengths</h3><ul>{_list_html(match_result.get('key_strengths', []))}</ul>"
            f"<h3>Gap Areas</h3><ul>{_list_html(match_result.get('gap_areas', []))}</ul>"
            f"<h3>Tailoring Recommendations</h3><ul>{_list_html(match_result.get('tailoring_recommendations', []))}</ul>"
            f"<h3>Experience Evaluation</h3><p>{html.escape(str(exp_eval.get('commentary') or ''))}</p>"
        )

        if tailored_resume:
            skills = tailored_resume.get("skills") or {}
            work_exp_html = ""
            for exp in tailored_resume.get("work_experience", []):
                bullets = "".join(f"<li>{html.escape(str(r))}</li>" for r in exp.get("responsibilities", []))
                work_exp_html += (
                    f"<h4>{html.escape(str(exp.get('job_title') or ''))} &mdash; {html.escape(str(exp.get('company') or ''))}</h4>"
                    f"<ul>{bullets}</ul>"
                )
            report_html += (
                f"<hr><h2>Tailored Resume</h2>"
                f"<p><i>{html.escape(str(tailored_resume.get('professional_summary') or ''))}</i></p>"
                f"<h3>Emphasized Skills</h3>"
                f"<p><b>Technical:</b> {html.escape(', '.join(skills.get('technical_skills', [])))}</p>"
                f"<h3>Work Experience</h3>{work_exp_html}"
            )

        browser = QTextBrowser()
        browser.setHtml(report_html)
        layout.addWidget(browser)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def _on_worker_progress(self, message: str) -> None:
        """Collects a Find Jobs progress message for the completion report, and shows it live."""
        self._run_log.append(message)
        self.status_bar.showMessage(message)

    def find_jobs(self) -> None:
        """Validates AI pipeline prerequisites, then starts the background Find Jobs worker."""
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            self.status_bar.showMessage("Find Jobs is already running — please wait for it to finish.")
            return

        error = check_pipeline_prerequisites()
        if error:
            QMessageBox.critical(self, "Cannot Run Find Jobs", error)
            return

        self.find_jobs_btn.setEnabled(False)
        self.find_jobs_action.setEnabled(False)
        self._run_log = []
        self.status_bar.showMessage("Finding jobs: fetching, extracting, matching, and tailoring in background...")

        self.worker = FindJobsWorker()
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self.on_find_jobs_finished)
        self.worker.start()

    def on_find_jobs_finished(
        self, total_fetched: int, total_new: int, total_matched: int, total_tailored: int, total_errors: int
    ) -> None:
        """Handles background Find Jobs pipeline completion."""
        self.find_jobs_btn.setEnabled(True)
        self.find_jobs_action.setEnabled(True)
        self.load_jobs()
        msg = (
            f"Find Jobs complete! Fetched: {total_fetched}, New: {total_new}, "
            f"Matched: {total_matched}, Tailored: {total_tailored}, Errors: {total_errors}."
        )
        self.status_bar.showMessage(msg)

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setWindowTitle("Find Jobs Complete")
        msg_box.setText(msg)
        if self._run_log:
            msg_box.setDetailedText("\n".join(self._run_log))
        msg_box.exec()

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
    run_app()
