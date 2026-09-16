"""
Modal progress dialog shown while FindJobsWorker fetches, extracts, matches, and
tailors jobs in the background. Shows live status/log output and lets the user
cancel the run safely (the worker itself guarantees no dangling DB rows).
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)


class ProcessingDialog(QDialog):
    """Modal dialog displaying live Find Jobs progress with a safe Cancel/Close button."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Finding Jobs...")
        self.setMinimumSize(520, 360)
        self.setModal(True)

        self._finished = False
        self._cancelling = False

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Starting...")
        self.status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view)

        self.action_btn = QPushButton("Cancel")
        self.action_btn.clicked.connect(self._on_action_clicked)
        layout.addWidget(self.action_btn)

    def add_message(self, message: str) -> None:
        """Appends a message to the log and updates the status line."""
        self.status_label.setText(message)
        self.log_view.appendPlainText(message)

    def _on_action_clicked(self) -> None:
        if self._finished:
            self.accept()
            return

        self._cancelling = True
        self.action_btn.setEnabled(False)
        self.action_btn.setText("Cancelling...")
        self.status_label.setText("Cancelling — finishing the current step safely...")
        self.cancel_requested.emit()

    def mark_finished(self, summary: str) -> None:
        """Switches the dialog into its terminal state once the worker has stopped."""
        self._finished = True
        self.status_label.setText(summary)
        self.log_view.appendPlainText(summary)
        self.action_btn.setEnabled(True)
        self.action_btn.setText("Close")

    def closeEvent(self, event) -> None:
        """Treats the window's close (X) button as Cancel while the worker is still running."""
        if not self._finished:
            event.ignore()
            if not self._cancelling:
                self._on_action_clicked()
            return
        event.accept()
