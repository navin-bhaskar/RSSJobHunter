"""
Main Application Window for RSS Job Hunter with Menu Bar (Settings -> RSS Feeds).
"""

import json
import sys
from typing import Optional, List, Dict, Any
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction
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
    update_job_structured_output,
    update_job_match_score,
)
from tools import fetch_rss_feed, get_universal_id
from gui.rss_feed_dialog import RSSFeedDialog


class RSSFetchWorker(QThread):
    """Worker thread for fetching RSS feeds in background without blocking GUI."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int)  # total_fetched, total_new

    def run(self) -> None:
        sources = get_all_rss_sources()
        if not sources:
            self.progress.emit("No RSS sources configured. Please add one in Settings -> RSS Feeds.")
            self.finished.emit(0, 0)
            return

        total_fetched = 0
        total_new = 0

        # Get existing job links/hashes for deduplication
        existing_jobs = get_all_jobs()
        existing_uids = {j.get("universal_id") for j in existing_jobs if j.get("universal_id")}
        existing_links = {j.get("job_link") for j in existing_jobs if j.get("job_link")}

        for source in sources:
            source_id = source["id"]
            source_name = source.get("name") or source.get("link")
            self.progress.emit(f"Fetching RSS feed: {source_name}...")

            try:
                feed = fetch_rss_feed(source["link"], max_items=20)
                items = feed.get("items", [])
                total_fetched += len(items)

                for item in items:
                    link = item.get("link", "")
                    uid = item.get("universal_id") or get_universal_id(link)

                    if uid in existing_uids or link in existing_links:
                        continue

                    desc = item.get("summary") or item.get("content") or ""
                    create_job(
                        job_link=link,
                        job_description=desc,
                        status="pending",
                        job_match_score=0,
                        application_link=link,
                        rss_source_id=source_id,
                        universal_id=uid,
                    )
                    existing_uids.add(uid)
                    existing_links.add(link)
                    total_new += 1
            except Exception as e:
                self.progress.emit(f"Error fetching '{source_name}': {e}")

        self.finished.emit(total_fetched, total_new)


class EnrichJobWorker(QThread):
    """Worker thread for running the LLM rss_agent to enrich a single job with structured data."""

    finished = pyqtSignal(int, object, str)  # job_id, StructuredJobListingSchema or None, error message

    def __init__(self, job: Dict[str, Any]):
        super().__init__()
        self.job = job

    def run(self) -> None:
        try:
            from agents import RSSJobAgent

            item = {
                "title": "",
                "link": self.job.get("job_link") or "",
                "author": "",
                "categories": [],
                "summary": self.job.get("job_description") or "",
            }
            agent = RSSJobAgent()
            structured = agent.process_feed_item(item, rss_source_id=self.job.get("rss_source_id"))
            self.finished.emit(self.job["id"], structured, "")
        except Exception as e:
            self.finished.emit(self.job["id"], None, str(e))


class MatchResumeWorker(QThread):
    """Worker thread for parsing the candidate resume and running ATS matching against a job."""

    finished = pyqtSignal(int, object, str)  # job_id, ATSMatchResult or None, error message

    def __init__(self, job: Dict[str, Any]):
        super().__init__()
        self.job = job

    def run(self) -> None:
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
    """Main Dashboard Window for RSS Job Hunter."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RSS Job Hunter - Dashboard")
        self.resize(1100, 700)

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

        fetch_action = QAction("&Fetch All RSS Feeds", self)
        fetch_action.setShortcut("Ctrl+R")
        fetch_action.triggered.connect(self.fetch_all_rss_feeds)
        file_menu.addAction(fetch_action)

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

        self.fetch_btn = QPushButton("⚡ Fetch RSS Feeds")
        self.fetch_btn.setStyleSheet("font-weight: bold; padding: 6px 12px; background-color: #2b5c8f;")
        self.fetch_btn.clicked.connect(self.fetch_all_rss_feeds)
        toolbar_layout.addWidget(self.fetch_btn)

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
        self.jobs_table.setColumnCount(5)
        self.jobs_table.setHorizontalHeaderLabels(["ID", "Job Link", "Status", "Match Score", "RSS Source"])
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
        self.enrich_btn = QPushButton("🤖 Enrich with AI")
        self.enrich_btn.setEnabled(False)
        self.enrich_btn.clicked.connect(self.enrich_selected_job)
        ai_actions_layout.addWidget(self.enrich_btn)

        self.match_btn = QPushButton("🎯 Match Resume")
        self.match_btn.setEnabled(False)
        self.match_btn.clicked.connect(self.match_resume_for_selected_job)
        ai_actions_layout.addWidget(self.match_btn)

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
        splitter.setSizes([600, 450])

        layout.addWidget(splitter)

    def open_rss_settings_dialog(self) -> None:
        """Opens Settings -> RSS Feeds dialog window."""
        dialog = RSSFeedDialog(self)
        dialog.exec()

    def load_jobs(self) -> None:
        """Loads jobs from database into the table widget based on filters."""
        status = self.status_filter.currentText()
        search = self.search_input.text().strip()

        jobs = get_all_jobs(status_filter=status, search=search)
        self.jobs_table.setRowCount(len(jobs))

        rss_sources = {s["id"]: s.get("name") or s.get("link") for s in get_all_rss_sources()}

        for row_idx, job in enumerate(jobs):
            id_item = QTableWidgetItem(str(job["id"]))
            link_item = QTableWidgetItem(job.get("job_link") or "")
            status_item = QTableWidgetItem(job.get("status") or "pending")
            score_item = QTableWidgetItem(str(job.get("job_match_score", 0)))

            source_name = rss_sources.get(job.get("rss_source_id"), "Direct / Manual")
            source_item = QTableWidgetItem(source_name)

            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.jobs_table.setItem(row_idx, 0, id_item)
            self.jobs_table.setItem(row_idx, 1, link_item)
            self.jobs_table.setItem(row_idx, 2, status_item)
            self.jobs_table.setItem(row_idx, 3, score_item)
            self.jobs_table.setItem(row_idx, 4, source_item)

        self.status_bar.showMessage(f"Loaded {len(jobs)} job records.")

    def on_job_selected(self) -> None:
        """Displays selected job details and notes on the right pane."""
        selected_ranges = self.jobs_table.selectedRanges()
        if not selected_ranges:
            self.job_title_label.setText("Select a job from the table to view details")
            self.job_desc_browser.clear()
            self.notes_list.clear()
            self.enrich_btn.setEnabled(False)
            self.match_btn.setEnabled(False)
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
        self.enrich_btn.setEnabled(True)
        self.match_btn.setEnabled(True)

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

    def _reselect_job(self, job_id: int) -> None:
        """Re-selects a job row by ID after the table has been reloaded."""
        for row in range(self.jobs_table.rowCount()):
            item = self.jobs_table.item(row, 0)
            if item and int(item.text()) == job_id:
                self.jobs_table.selectRow(row)
                break

    def enrich_selected_job(self) -> None:
        """Runs the LLM rss_agent on the selected job to extract structured details."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        job = get_job(job_id)
        if not job:
            return

        self.enrich_btn.setEnabled(False)
        self.status_bar.showMessage("Enriching job with AI (this may take a moment)...")

        self.enrich_worker = EnrichJobWorker(job)
        self.enrich_worker.finished.connect(self.on_enrich_finished)
        self.enrich_worker.start()

    def on_enrich_finished(self, job_id: int, structured: Any, error: str) -> None:
        """Handles completion of the AI enrichment worker."""
        self.enrich_btn.setEnabled(True)
        if error:
            QMessageBox.critical(self, "Enrichment Failed", f"Could not enrich job: {error}")
            self.status_bar.showMessage("Enrichment failed.")
            return

        update_job_structured_output(job_id, structured.to_json())
        self.status_bar.showMessage("Job enriched successfully.")
        self.load_jobs()
        self._reselect_job(job_id)

    def match_resume_for_selected_job(self) -> None:
        """Parses the candidate resume and runs an ATS match against the selected job."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        job = get_job(job_id)
        if not job:
            return

        if not (job.get("job_description") or "").strip():
            QMessageBox.warning(self, "No Description", "This job has no description text to match against.")
            return

        self.match_btn.setEnabled(False)
        self.status_bar.showMessage("Parsing resume & running ATS match (this may take a moment)...")

        self.match_worker = MatchResumeWorker(job)
        self.match_worker.finished.connect(self.on_match_finished)
        self.match_worker.start()

    def on_match_finished(self, job_id: int, result: Any, error: str) -> None:
        """Handles completion of the ATS matching worker."""
        self.match_btn.setEnabled(True)
        if error:
            QMessageBox.critical(self, "ATS Match Failed", f"Could not run ATS match: {error}")
            self.status_bar.showMessage("ATS match failed.")
            return

        update_job_match_score(job_id, result.match_score)
        self.status_bar.showMessage(f"ATS match complete: {result.match_score}/100 ({result.fit_level}).")
        self.load_jobs()
        self._reselect_job(job_id)
        self._show_match_result_dialog(result)

    def _show_match_result_dialog(self, result: Any) -> None:
        """Displays a detailed ATS match result in a modal dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"ATS Match Result: {result.match_score}/100 ({result.fit_level})")
        dialog.setMinimumSize(600, 500)
        layout = QVBoxLayout(dialog)

        def _list_html(items: List[str]) -> str:
            return "".join(f"<li>{i}</li>" for i in items) or "<li>None</li>"

        browser = QTextBrowser()
        html = (
            f"<h2>{result.match_score}/100 &mdash; {result.fit_level}</h2>"
            f"<p>{result.executive_summary}</p>"
            f"<h3>Matching Skills</h3><ul>{_list_html(result.matching_skills)}</ul>"
            f"<h3>Missing Skills</h3><ul>{_list_html(result.missing_skills)}</ul>"
            f"<h3>Key Strengths</h3><ul>{_list_html(result.key_strengths)}</ul>"
            f"<h3>Gap Areas</h3><ul>{_list_html(result.gap_areas)}</ul>"
            f"<h3>Tailoring Recommendations</h3><ul>{_list_html(result.tailoring_recommendations)}</ul>"
            f"<h3>Experience Evaluation</h3><p>{result.experience_evaluation.commentary}</p>"
        )
        browser.setHtml(html)
        layout.addWidget(browser)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def fetch_all_rss_feeds(self) -> None:
        """Starts background worker to fetch all RSS feeds."""
        self.fetch_btn.setEnabled(False)
        self.status_bar.showMessage("Fetching RSS feeds in background...")

        self.worker = RSSFetchWorker()
        self.worker.progress.connect(self.status_bar.showMessage)
        self.worker.finished.connect(self.on_fetch_finished)
        self.worker.start()

    def on_fetch_finished(self, total_fetched: int, total_new: int) -> None:
        """Handles background fetch completion."""
        self.fetch_btn.setEnabled(True)
        self.load_jobs()
        msg = f"Fetch complete! Items fetched: {total_fetched}, New jobs added: {total_new}."
        self.status_bar.showMessage(msg)
        QMessageBox.information(self, "RSS Feed Sync Complete", msg)

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
