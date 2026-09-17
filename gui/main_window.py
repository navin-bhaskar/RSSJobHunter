"""
Main Application Window for RSS Job Hunter with Menu Bar (Settings -> RSS Feeds).
"""

import html
import json
import os
import subprocess
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
    add_note,
    get_all_rss_sources,
    update_job_tailored_resume,
    update_job_tailored_resume_pdf_path,
    update_job_status,
    delete_job,
    soft_delete_job,
)
from tools import render_resume_pdf, ResumePDFError
from gui.rss_feed_dialog import RSSFeedDialog
from gui.processing_dialog import ProcessingDialog
from pipeline import (
    check_llm_prerequisites,
    check_pipeline_prerequisites,
    run_find_jobs_pipeline,
)

RESUME_PDF_OUTPUT_DIR = Path("generated_resumes")
from dotenv import load_dotenv

load_dotenv()


class FindJobsWorker(QThread):
    """
    Worker thread that drives run_find_jobs_pipeline() (see pipeline.py) in the
    background and re-emits its progress as Qt signals for the GUI.

    Cancellation is cooperative (QThread.requestInterruption()): the pipeline checks
    it between feeds, between items, and immediately after a job row is created.
    """

    progress = pyqtSignal(str)
    job_saved = pyqtSignal()
    finished = pyqtSignal(int, int, int, int, int, bool)
    # total_fetched, total_new, total_matched, total_tailored, total_errors, cancelled

    def run(self) -> None:
        result = run_find_jobs_pipeline(
            progress_callback=self.progress.emit,
            on_job_saved=self.job_saved.emit,
            should_cancel=self.isInterruptionRequested,
        )
        self.finished.emit(
            result.total_fetched,
            result.total_new,
            result.total_matched,
            result.total_tailored,
            result.total_errors,
            result.cancelled,
        )


class TailorResumeWorker(QThread):
    """
    Worker thread that tailors the candidate's resume for a single selected job
    (regardless of its match score) and renders the result to a PDF file, saving
    the PDF's path back onto the job record.
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(int, bool, str)
    # job_id, success, pdf_path (on success) or error message (on failure)

    def __init__(self, job_id: int):
        super().__init__()
        self.job_id = job_id

    def run(self) -> None:
        from agents import ResumeAgent, TailorAgent

        job = get_job(self.job_id)
        if not job:
            self.finished.emit(self.job_id, False, f"Job #{self.job_id} no longer exists.")
            return

        job_title = None
        company_name = None
        job_description = job.get("job_description") or ""
        structured_raw = job.get("structured_output")
        if structured_raw:
            try:
                structured = json.loads(structured_raw)
                job_title = structured.get("title")
                company_name = structured.get("company")
                job_description = structured.get("clean_description") or job_description
            except Exception:
                pass

        try:
            self.progress.emit("Parsing candidate resume...")
            resume_schema, _from_cache = ResumeAgent().parse_resume()

            self.progress.emit(f"Tailoring resume for job #{self.job_id}...")
            tailored = TailorAgent().tailor_resume(
                resume_data=resume_schema,
                job_description=job_description,
                job_title=job_title,
                company_name=company_name,
            )
            update_job_tailored_resume(self.job_id, tailored.model_dump_json())

            self.progress.emit("Rendering resume PDF...")
            output_path = RESUME_PDF_OUTPUT_DIR / f"job_{self.job_id}.pdf"
            pdf_path = render_resume_pdf(tailored, output_path)
            update_job_tailored_resume_pdf_path(self.job_id, pdf_path)

            self.finished.emit(self.job_id, True, pdf_path)
        except ResumePDFError as e:
            self.finished.emit(self.job_id, False, f"Resume was tailored but PDF rendering failed: {e}")
        except Exception as e:
            self.finished.emit(self.job_id, False, str(e))


class MainWindow(QMainWindow):
    """Main Dashboard Window for RSS Job Hunter."""

    JOB_STATUSES = ["pending", "applied", "interviewing", "selected", "rejected", "withdrawn"]

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
        self.status_filter.addItems(["All"] + self.JOB_STATUSES)
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
        self.jobs_table.setColumnCount(5)
        self.jobs_table.setHorizontalHeaderLabels(
            ["Job Link", "Status", "Match Score", "Tailored", "RSS Source"]
        )
        self.jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
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

        status_row_layout = QHBoxLayout()
        status_row_layout.addWidget(QLabel("Status:"))
        self.job_status_combo = QComboBox()
        self.job_status_combo.addItems(self.JOB_STATUSES)
        self.job_status_combo.setEnabled(False)
        self.job_status_combo.currentTextChanged.connect(self.on_job_status_changed)
        status_row_layout.addWidget(self.job_status_combo)
        status_row_layout.addStretch()
        job_info_layout.addLayout(status_row_layout)

        self.job_desc_browser = QTextBrowser()
        self.job_desc_browser.setOpenExternalLinks(True)
        job_info_layout.addWidget(self.job_desc_browser)

        ai_actions_layout = QHBoxLayout()
        self.view_report_btn = QPushButton("📄 View AI Report")
        self.view_report_btn.setEnabled(False)
        self.view_report_btn.clicked.connect(self.view_ai_report)
        ai_actions_layout.addWidget(self.view_report_btn)

        self.tailor_resume_btn = QPushButton("📝 Tailor Resume (PDF)")
        self.tailor_resume_btn.setEnabled(False)
        self.tailor_resume_btn.clicked.connect(self.tailor_resume_for_selected_job)
        # Fixed width (sized for the longer of its two labels) so swapping to "Tailoring..."
        # doesn't change the button's size hint and reflow the rest of this row.
        metrics = self.tailor_resume_btn.fontMetrics()
        label_width = max(metrics.horizontalAdvance(t) for t in ("📝 Tailor Resume (PDF)", "Tailoring..."))
        self.tailor_resume_btn.setMinimumWidth(label_width + 40)
        ai_actions_layout.addWidget(self.tailor_resume_btn)

        self.open_resume_pdf_btn = QPushButton("📂 Open PDF")
        self.open_resume_pdf_btn.setEnabled(False)
        self.open_resume_pdf_btn.clicked.connect(self.open_tailored_resume_pdf)
        ai_actions_layout.addWidget(self.open_resume_pdf_btn)

        self.show_resume_pdf_in_explorer_btn = QPushButton("🗂 Show in Explorer")
        self.show_resume_pdf_in_explorer_btn.setEnabled(False)
        self.show_resume_pdf_in_explorer_btn.clicked.connect(self.show_tailored_resume_pdf_in_explorer)
        ai_actions_layout.addWidget(self.show_resume_pdf_in_explorer_btn)

        self.remove_job_btn = QPushButton("🗑 Remove")
        self.remove_job_btn.setEnabled(False)
        self.remove_job_btn.setStyleSheet("color: #ff5555;")
        self.remove_job_btn.clicked.connect(self.remove_selected_job)
        ai_actions_layout.addWidget(self.remove_job_btn)

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

            link_item = QTableWidgetItem(job.get("job_link") or "")
            link_item.setData(Qt.ItemDataRole.UserRole, job["id"])
            status_item = QTableWidgetItem(job.get("status") or "pending")

            score_item = QTableWidgetItem(str(score))
            score_item.setData(Qt.ItemDataRole.EditRole, score)

            tailored_item = QTableWidgetItem("Yes" if is_tailored else "No")

            source_name = rss_sources.get(job.get("rss_source_id"), "Direct / Manual")
            source_item = QTableWidgetItem(source_name)

            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tailored_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            row_items = [link_item, status_item, score_item, tailored_item, source_item]
            if has_match:
                bg, fg = self._score_band_colors(score)
                for item in row_items:
                    item.setBackground(bg)
                    item.setForeground(fg)

            for col, item in enumerate(row_items):
                self.jobs_table.setItem(row_idx, col, item)

        self.jobs_table.setSortingEnabled(True)
        if not self._initial_sort_applied:
            self.jobs_table.sortItems(2, Qt.SortOrder.DescendingOrder)
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
            self.remove_job_btn.setEnabled(False)
            self.tailor_resume_btn.setEnabled(False)
            self.open_resume_pdf_btn.setEnabled(False)
            self.show_resume_pdf_in_explorer_btn.setEnabled(False)
            self.job_status_combo.setEnabled(False)
            return

        row = selected_ranges[0].topRow()
        job_id = self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

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

        pdf_path = job.get("tailored_resume_pdf_path")
        pdf_line = f"<b>Tailored Resume PDF:</b> {html.escape(pdf_path)}<br>" if pdf_path else ""

        content_html = (
            f"<b>Job Link:</b> <a href='{link}'>{link}</a><br>"
            f"<b>Status:</b> {job.get('status')}<br>"
            f"<b>Match Score:</b> {job.get('job_match_score')}<br>"
            f"<b>RSS Source:</b> {html.escape(str(source_str)) if source_str else 'N/A'}<br>"
            f"{pdf_line}"
            f"<hr>"
            f"{structured_html}"
            f"<h3>Job Description</h3>"
            f"<p>{html.escape(job.get('job_description') or 'No description text provided.')}</p>"
        )
        self.job_desc_browser.setHtml(content_html)

        current_status = job.get("status") or "pending"
        self.job_status_combo.blockSignals(True)
        if self.job_status_combo.findText(current_status) < 0:
            self.job_status_combo.addItem(current_status)
        self.job_status_combo.setCurrentText(current_status)
        self.job_status_combo.blockSignals(False)
        self.job_status_combo.setEnabled(True)

        self.view_report_btn.setEnabled(bool(job.get("match_result")))
        self.remove_job_btn.setEnabled(True)
        tailor_running = getattr(self, "tailor_worker", None) is not None and self.tailor_worker.isRunning()
        self.tailor_resume_btn.setEnabled(not tailor_running)
        self.open_resume_pdf_btn.setEnabled(bool(pdf_path))
        self.show_resume_pdf_in_explorer_btn.setEnabled(bool(pdf_path))

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
        job_id = self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

        text, ok = QInputDialog.getText(self, "Add Job Note", "Enter note text:")
        if ok and text.strip():
            add_note(job_id, text.strip())
            self.on_job_selected()
            self.status_bar.showMessage("Note added successfully.")

    def on_job_status_changed(self, new_status: str) -> None:
        """Persists the selected job's status change from the details pane combo box
        and refreshes the table row (and its score-band color) to reflect it."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            return

        update_job_status(job_id, new_status)
        self.load_jobs()
        self._select_job_row(job_id)
        self.status_bar.showMessage(f"Job #{job_id} status updated to '{new_status}'.")

    def _get_selected_job_id(self) -> Optional[int]:
        """Returns the ID of the currently selected job in the table, or None."""
        selected_ranges = self.jobs_table.selectedRanges()
        if not selected_ranges:
            return None
        item = self.jobs_table.item(selected_ranges[0].topRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def remove_selected_job(self) -> None:
        """Soft-deletes the selected job: hides it from the table without deleting its
        record, so a future Find Jobs run won't re-fetch and re-populate it."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Remove job #{job_id} from the list? It won't be re-added by future Find Jobs runs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        soft_delete_job(job_id)
        self.load_jobs()
        self.on_job_selected()
        self.status_bar.showMessage(f"Job #{job_id} removed.")

    def _select_job_row(self, job_id: int) -> None:
        """Re-selects the table row for the given job_id, e.g. after a load_jobs() refresh.

        Always explicitly refreshes the Job Details pane afterward rather than relying on
        itemSelectionChanged: Qt does not fire that signal when the row we're re-selecting
        has the same index as the row that was already selected (e.g. a job's sort position
        is unchanged after tailoring its resume), which would otherwise leave the pane
        showing stale data.
        """
        for row in range(self.jobs_table.rowCount()):
            item = self.jobs_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == job_id:
                self.jobs_table.selectRow(row)
                break
        self.on_job_selected()

    def tailor_resume_for_selected_job(self) -> None:
        """Tailors the candidate's resume for the selected job (regardless of its match
        score) and renders it to a PDF file in the background."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            QMessageBox.warning(self, "Selection Error", "Please select a job first.")
            return

        if getattr(self, "tailor_worker", None) is not None and self.tailor_worker.isRunning():
            self.status_bar.showMessage("A resume is already being tailored — please wait for it to finish.")
            return

        error = check_llm_prerequisites()
        if error:
            QMessageBox.critical(self, "Cannot Tailor Resume", error)
            return

        self.tailor_resume_btn.setEnabled(False)
        self.tailor_resume_btn.setText("Tailoring...")

        self.tailor_worker = TailorResumeWorker(job_id)
        self.tailor_worker.progress.connect(self.status_bar.showMessage)
        self.tailor_worker.finished.connect(self.on_tailor_resume_finished)
        self.tailor_worker.start()

    def on_tailor_resume_finished(self, job_id: int, success: bool, message: str) -> None:
        """Handles TailorResumeWorker completion: refreshes the table/details and re-enables the button."""
        self.tailor_resume_btn.setEnabled(True)
        self.tailor_resume_btn.setText("📝 Tailor Resume (PDF)")

        if not success:
            self.status_bar.showMessage(f"Tailoring failed for job #{job_id}.")
            QMessageBox.critical(self, "Tailor Resume Failed", message)
            return

        self.status_bar.showMessage(f"Resume PDF ready for job #{job_id}: {message}")
        self.load_jobs()
        self._select_job_row(job_id)

    def _get_selected_resume_pdf_path(self) -> Optional[str]:
        """Returns the selected job's tailored resume PDF path if it exists on disk,
        else warns the user and returns None."""
        job_id = self._get_selected_job_id()
        if job_id is None:
            return None

        job = get_job(job_id)
        pdf_path = job.get("tailored_resume_pdf_path") if job else None
        if not pdf_path or not Path(pdf_path).exists():
            QMessageBox.warning(
                self, "PDF Not Found", "No tailored resume PDF found for this job. Try tailoring it again."
            )
            return None
        return pdf_path

    def open_tailored_resume_pdf(self) -> None:
        """Opens the selected job's tailored resume PDF with the OS default viewer."""
        pdf_path = self._get_selected_resume_pdf_path()
        if not pdf_path:
            return

        try:
            os.startfile(pdf_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF: {e}")

    def show_tailored_resume_pdf_in_explorer(self) -> None:
        """Opens Windows Explorer with the selected job's tailored resume PDF pre-selected."""
        pdf_path = self._get_selected_resume_pdf_path()
        if not pdf_path:
            return

        try:
            subprocess.run(["explorer", f"/select,{Path(pdf_path).resolve()}"])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open Explorer: {e}")

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
        if getattr(self, "processing_dialog", None) is not None:
            self.processing_dialog.add_message(message)

    def find_jobs(self) -> None:
        """Validates AI pipeline prerequisites, then runs the background Find Jobs worker behind a modal progress dialog."""
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
        self.processing_dialog = ProcessingDialog(self)
        self.processing_dialog.cancel_requested.connect(self.worker.requestInterruption)

        self.worker.progress.connect(self._on_worker_progress)
        self.worker.job_saved.connect(self.load_jobs)
        self.worker.finished.connect(self.on_find_jobs_finished)
        self.worker.start()

        self.processing_dialog.exec()

    def on_find_jobs_finished(
        self,
        total_fetched: int,
        total_new: int,
        total_matched: int,
        total_tailored: int,
        total_errors: int,
        cancelled: bool,
    ) -> None:
        """Handles background Find Jobs pipeline completion (or cancellation)."""
        self.find_jobs_btn.setEnabled(True)
        self.find_jobs_action.setEnabled(True)
        self.load_jobs()

        prefix = "Cancelled." if cancelled else "Find Jobs complete!"
        msg = (
            f"{prefix} Fetched: {total_fetched}, New: {total_new}, "
            f"Matched: {total_matched}, Tailored: {total_tailored}, Errors: {total_errors}."
        )
        self.status_bar.showMessage(msg)

        if getattr(self, "processing_dialog", None) is not None:
            self.processing_dialog.mark_finished(msg)

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
