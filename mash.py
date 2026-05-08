#!/usr/bin/env python3
"""Mash 2.0 — Dynamic Island Text Agent.
Run: python mash.py
"""
import sys
import os
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ui.notch_window import NotchWindow
from utils.logger import logger


def main():
    logger.info("Starting Mash 2.0...")
    # Force X11 backend to allow absolute window positioning and always-on-top on Wayland
    os.environ["QT_QPA_PLATFORM"] = "xcb"

    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Mash")
    app.setApplicationDisplayName("Mash")
    app.setQuitOnLastWindowClosed(True)

    # Set app icon
    icon_path = Path(__file__).parent / "icon.png"
    if not icon_path.exists():
        icon_path = Path(__file__).parent / "icon.jpeg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = NotchWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
