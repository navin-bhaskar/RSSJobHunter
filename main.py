"""
Main entry point for RSS Job Hunter GUI Application.
"""

import sys
from dotenv import load_dotenv
from PyQt6.QtWidgets import QApplication
from database import init_db
from gui.main_window import MainWindow

try:
    import qdarktheme
    HAS_QDARKTHEME = True
except ImportError:
    HAS_QDARKTHEME = False


def main():
    """Initializes database and runs the modern PyQt6 GUI application."""
    load_dotenv()
    init_db()

    app = QApplication(sys.argv)

    if HAS_QDARKTHEME:
        try:
            qdarktheme.setup_theme("dark")
        except Exception:
            pass

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
