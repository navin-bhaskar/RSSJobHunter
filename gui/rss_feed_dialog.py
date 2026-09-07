"""
RSS Feed Settings Dialog for managing RSS Sources in the rss_sources table.
Accessible via Menu Bar: Settings -> RSS Feeds.
"""

from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QGroupBox,
    QFormLayout,
)

from database import (
    create_rss_source,
    get_all_rss_sources,
    delete_rss_source,
    get_rss_source_by_link,
)
from tools import fetch_rss_feed


class RSSFeedDialog(QDialog):
    """Dialog window for viewing, adding, testing, and deleting RSS feeds."""

    def __init__(self, parent: Optional[QDialog] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings -> RSS Feeds Management")
        self.setMinimumSize(750, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        self._init_ui()
        self.load_rss_sources()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)

        # Header Label
        header = QLabel("Manage RSS Sources")
        header.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 5px;")
        main_layout.addWidget(header)

        # Form Group for Adding New Source
        form_group = QGroupBox("Add New RSS Feed Source")
        form_layout = QFormLayout(form_group)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. WeWorkRemotely Full-Stack")
        form_layout.addRow("Feed Name:", self.name_input)

        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("e.g. https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss")
        form_layout.addRow("RSS Feed Link:", self.link_input)

        btn_box = QHBoxLayout()
        self.add_btn = QPushButton("Add RSS Source")
        self.add_btn.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.add_btn.clicked.connect(self.add_source)

        self.test_btn = QPushButton("Test Feed Link")
        self.test_btn.setStyleSheet("padding: 6px 12px;")
        self.test_btn.clicked.connect(self.test_feed_connection)

        btn_box.addWidget(self.add_btn)
        btn_box.addWidget(self.test_btn)
        btn_box.addStretch()

        form_layout.addRow("", btn_box)
        main_layout.addWidget(form_group)

        # Table of Registered Sources
        table_group = QGroupBox("Registered RSS Sources")
        table_layout = QVBoxLayout(table_group)

        self.sources_table = QTableWidget()
        self.sources_table.setColumnCount(4)
        self.sources_table.setHorizontalHeaderLabels(["ID", "Name", "Feed Link", "Created At"])
        self.sources_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.sources_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.sources_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sources_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        table_layout.addWidget(self.sources_table)

        # Bottom Actions Layout
        bottom_layout = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Selected Source")
        self.delete_btn.setStyleSheet("color: #ff5555; font-weight: bold; padding: 6px 12px;")
        self.delete_btn.clicked.connect(self.delete_selected_source)

        self.refresh_btn = QPushButton("Refresh List")
        self.refresh_btn.clicked.connect(self.load_rss_sources)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)

        bottom_layout.addWidget(self.delete_btn)
        bottom_layout.addWidget(self.refresh_btn)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.close_btn)

        table_layout.addLayout(bottom_layout)
        main_layout.addWidget(table_group)

    def load_rss_sources(self) -> None:
        """Loads and populates all RSS sources from the database table."""
        sources = get_all_rss_sources()
        self.sources_table.setRowCount(len(sources))

        for row_idx, source in enumerate(sources):
            id_item = QTableWidgetItem(str(source["id"]))
            name_item = QTableWidgetItem(source.get("name") or "Unnamed Feed")
            link_item = QTableWidgetItem(source.get("link") or "")
            created_item = QTableWidgetItem(str(source.get("created_at") or ""))

            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.sources_table.setItem(row_idx, 0, id_item)
            self.sources_table.setItem(row_idx, 1, name_item)
            self.sources_table.setItem(row_idx, 2, link_item)
            self.sources_table.setItem(row_idx, 3, created_item)

    def add_source(self) -> None:
        """Handles adding a new RSS feed source to the database."""
        name = self.name_input.text().strip()
        link = self.link_input.text().strip()

        if not link:
            QMessageBox.warning(self, "Validation Error", "Please provide a valid RSS feed URL link.")
            return

        if get_rss_source_by_link(link):
            QMessageBox.warning(self, "Duplicate Error", "An RSS feed with this link already exists!")
            return

        try:
            source_id = create_rss_source(link=link, name=name if name else None)
            QMessageBox.information(
                self, "Success", f"RSS Source added successfully (ID: {source_id})!"
            )
            self.name_input.clear()
            self.link_input.clear()
            self.load_rss_sources()
        except Exception as err:
            QMessageBox.critical(self, "Database Error", f"Failed to add RSS source: {err}")

    def test_feed_connection(self) -> None:
        """Tests fetching and parsing the RSS feed from the link input."""
        link = self.link_input.text().strip()
        if not link:
            # If input is empty, test selected row from table
            selected_rows = self.sources_table.selectedItems()
            if selected_rows:
                row = selected_rows[0].row()
                link = self.sources_table.item(row, 2).text()

        if not link:
            QMessageBox.warning(
                self, "Validation Error", "Please enter a feed URL or select a feed from the table to test."
            )
            return

        try:
            feed = fetch_rss_feed(link, max_items=3)
            feed_title = feed.get("feed_title", "Untitled Feed")
            total_items = feed.get("total_items", 0)

            sample_titles = "\n".join([f"• {item['title']}" for item in feed.get("items", [])])
            msg = (
                f"Successfully fetched feed!\n\n"
                f"Title: {feed_title}\n"
                f"Items Found: {total_items}\n\n"
                f"Sample Job Headlines:\n{sample_titles}"
            )
            QMessageBox.information(self, "Feed Test Success", msg)
        except Exception as err:
            QMessageBox.critical(self, "Feed Test Failed", f"Could not parse RSS feed from '{link}':\n\n{err}")

    def delete_selected_source(self) -> None:
        """Deletes the selected RSS source from the database."""
        selected_ranges = self.sources_table.selectedRanges()
        if not selected_ranges:
            QMessageBox.warning(self, "Selection Error", "Please select an RSS source to delete.")
            return

        row = selected_ranges[0].topRow()
        source_id = int(self.sources_table.item(row, 0).text())
        source_name = self.sources_table.item(row, 1).text()

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete RSS Source '{source_name}' (ID: {source_id})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_rss_source(source_id)
                self.load_rss_sources()
                QMessageBox.information(self, "Deleted", "RSS Source deleted successfully.")
            except Exception as err:
                QMessageBox.critical(self, "Error", f"Failed to delete RSS source: {err}")
