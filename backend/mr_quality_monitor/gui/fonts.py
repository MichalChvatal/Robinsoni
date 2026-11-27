"""Fallback for headless/sandbox Qt platforms without native font discovery."""

import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication


def ensure_font() -> None:
    if QFontDatabase.families():
        return
    candidates = (
        Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / "segoeui.ttf",
        Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / "seguisym.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.families()
    if families:
        QGuiApplication.instance().setFont(
            QFont("Segoe UI" if "Segoe UI" in families else families[0], 10)
        )
