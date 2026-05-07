"""InputBar — hover-reveal text input at the bottom of the expanded notch."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QFileDialog
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QKeyEvent
import os

class InputBar(QWidget):
    """Slim text input that animates in/out via opacity."""
    submitted = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._attach_path = ""
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(8)

        self._attach_btn = QPushButton("+")
        self._attach_btn.setFixedSize(34, 34)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.clicked.connect(self._toggle_attachment)
        self._attach_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.1);
                border: none; border-radius: 17px;
                color: #ffffff; font-size: 18px; font-weight: bold;
            }
            QPushButton:hover { background: rgba(255,255,255,0.2); }
            QPushButton:pressed { background: rgba(255,255,255,0.3); }
        """)

        self._field = QLineEdit()
        self._field.setPlaceholderText("Type a message…")
        self._field.setFont(QFont("Inter", 10))
        self._field.returnPressed.connect(self._send)
        self._field.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 18px;
                min-height: 36px;
                max-height: 36px;
                padding: 0px 16px;
                color: #ffffff;
                selection-background-color: #555555;
            }
            QLineEdit:focus {
                border: 1px solid rgba(255,255,255,0.6);
                background: rgba(255,255,255,0.12);
            }
        """)

        self._btn = QPushButton("↑")
        self._btn.setFixedSize(34, 34)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._send)
        self._btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                border: none; border-radius: 17px;
                color: #000000; font-size: 16px; font-weight: bold;
            }
            QPushButton:hover { background: #e0e0e0; }
            QPushButton:pressed { background: #a0a0a0; }
            QPushButton:disabled { background: rgba(255,255,255,0.3); }
        """)

        layout.addWidget(self._attach_btn)
        layout.addWidget(self._field)
        layout.addWidget(self._btn)

    def _toggle_attachment(self):
        if self._attach_path:
            self._attach_path = ""
            self._attach_btn.setText("+")
            self._field.setPlaceholderText("Type a message…")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Attach File", "", "Images & Docs (*.png *.jpg *.jpeg *.pdf *.txt *.csv *.md)"
            )
            if path:
                self._attach_path = path
                self._attach_btn.setText("×")
                fname = os.path.basename(path)
                self._field.setPlaceholderText(f"[Attached: {fname[:15]}] Type a message…")

    def _send(self):
        text = self._field.text().strip()
        if text or self._attach_path:
            self._field.clear()
            attached = self._attach_path
            self._attach_path = ""
            self._attach_btn.setText("+")
            self._field.setPlaceholderText("Type a message…")
            self.submitted.emit(text, attached)

    def set_enabled(self, enabled: bool):
        self._field.setEnabled(enabled)
        self._btn.setEnabled(enabled)
        self._field.setPlaceholderText(
            "Mash is thinking…" if not enabled else "Type a message…"
        )

    def focus(self):
        self._field.setFocus()
