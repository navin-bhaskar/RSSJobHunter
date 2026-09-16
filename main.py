"""
Main entry point for RSS Job Hunter: launches the PyQt6 GUI by default, or the
headless polling runner with --runner.
"""

import argparse
import sys


def run_gui():
    """Initializes database and runs the modern PyQt6 GUI application."""
    from dotenv import load_dotenv
    from PyQt6.QtWidgets import QApplication
    from database import init_db
    from gui.main_window import MainWindow

    load_dotenv()
    init_db()

    app = QApplication(sys.argv)

    try:
        import qdarktheme
        qdarktheme.setup_theme("dark")
    except Exception:
        pass

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def main():
    parser = argparse.ArgumentParser(description="RSS Job Hunter")
    parser.add_argument(
        "--runner",
        action="store_true",
        help="Run headless: poll RSS feeds and run the Find Jobs pipeline on a "
        "timer (RUNNER_INTERVAL_MINUTES) instead of launching the GUI.",
    )
    args = parser.parse_args()

    if args.runner:
        import runner
        runner.main()
    else:
        run_gui()


if __name__ == "__main__":
    main()
