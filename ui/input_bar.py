"""InputBar — text input with slash-command autocomplete."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
    QPushButton, QFileDialog, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QFont, QKeyEvent
import os

# ── Slash command registry ────────────────────────────────────────────────────
# (name, description, arg_hint)
SLASH_COMMANDS = [
    ("/stop",         "Stop generation immediately",         ""),
    ("/code",         "Build an app or feature",             "what to build"),
    ("/debug",        "Debug errors and fix issues",         "describe the problem"),
    ("/run",          "Run the current project",             ""),
    ("/requirements", "Install project dependencies",        ""),
    ("/projects",     "List all projects",                   ""),
    ("/switch",       "Switch to another project",           "project name"),
    ("/open",         "Open project in VS Code",             ""),
    ("/clear",        "Clear conversation history",          ""),
    ("/chat",         "General question or chat",            "your message"),
    ("/explain",      "Explain code or a concept",           "topic or code"),
    ("/git",          "Show git status",                     ""),
]


class _SlashLineEdit(QLineEdit):
    """QLineEdit that emits Up/Down/Escape signals for menu navigation."""
    key_up     = pyqtSignal()
    key_down   = pyqtSignal()
    key_escape = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.key_up.emit()
        elif key == Qt.Key.Key_Down:
            self.key_down.emit()
        elif key == Qt.Key.Key_Escape:
            self.key_escape.emit()
        else:
            super().keyPressEvent(event)


class _CmdRow(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, name, desc, arg, parent=None):
        super().__init__(parent)
        self.command = name
        self._sel = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 16, 0)
        h.setSpacing(10)

        # Command name
        cmd_lbl = QLabel(name)
        cmd_lbl.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        cmd_lbl.setStyleSheet("background:transparent; color:#60a5fa;")
        h.addWidget(cmd_lbl)

        # Arg hint
        if arg:
            arg_lbl = QLabel(arg)
            arg_lbl.setFont(QFont("Inter", 9))
            arg_lbl.setStyleSheet("background:transparent; color:rgba(255,255,255,0.28);")
            h.addWidget(arg_lbl)

        h.addStretch()

        # Description
        desc_lbl = QLabel(desc)
        desc_lbl.setFont(QFont("Inter", 9))
        desc_lbl.setStyleSheet("background:transparent; color:rgba(255,255,255,0.38);")
        h.addWidget(desc_lbl)

        self._refresh()

    def set_selected(self, v: bool):
        self._sel = v
        self._refresh()

    def _refresh(self):
        # Slate-800/900 style for selected items
        self.setStyleSheet(
            "background: rgba(255, 255, 255, 0.08); border-radius:6px;" if self._sel
            else "background:transparent; border-radius:6px;"
        )

    def mousePressEvent(self, e):
        self.clicked.emit(self.command)
        super().mousePressEvent(e)


class SlashMenu(QFrame):
    """Floating popup shown above InputBar when '/' is typed in Coding mode."""
    command_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        # Solid dark card matching Mash panel colour
        self.setStyleSheet("""
            QFrame {
                background: #0a0a0a;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 12px;
            }
        """)
        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(5, 5, 5, 5)
        self._vbox.setSpacing(1)
        self._rows: list[_CmdRow] = []
        self._cursor = 0
        self._populate(SLASH_COMMANDS)

    def _populate(self, cmds):
        for r in self._rows:
            self._vbox.removeWidget(r)
            r.deleteLater()
        self._rows = []
        for name, desc, arg in cmds:
            row = _CmdRow(name, desc, arg)
            row.clicked.connect(self.command_selected)
            self._vbox.addWidget(row)
            self._rows.append(row)
        self._cursor = 0
        if self._rows:
            self._rows[0].set_selected(True)
        self.adjustSize()

    def filter(self, query: str):
        q = query.lower()
        filtered = [c for c in SLASH_COMMANDS
                    if not q or q in c[0] or q in c[1].lower()]
        self._populate(filtered)

    def move_cursor(self, delta: int):
        if not self._rows:
            return
        self._rows[self._cursor].set_selected(False)
        self._cursor = (self._cursor + delta) % len(self._rows)
        self._rows[self._cursor].set_selected(True)

    def accept_current(self) -> str | None:
        if self._rows:
            cmd = self._rows[self._cursor].command
            self.command_selected.emit(cmd)
            return cmd
        return None

    @property
    def has_results(self):
        return bool(self._rows)


# ── InputBar ──────────────────────────────────────────────────────────────────

_BTN_SEND = """
    QPushButton {
        background:#ffffff; border:none; border-radius:17px;
        color:#000000; font-size:16px; font-weight:bold;
    }
    QPushButton:hover { background:#e0e0e0; }
    QPushButton:pressed { background:#a0a0a0; }
"""
_BTN_STOP = """
    QPushButton {
        background:#ff4444; border:none; border-radius:17px;
        color:#ffffff; font-size:14px; font-weight:bold;
    }
    QPushButton:hover { background:#ff6666; }
"""


class InputBar(QWidget):
    submitted         = pyqtSignal(str, str)   # (text, attachment)
    command_triggered = pyqtSignal(str, str)   # (command, argument)
    stopped           = pyqtSignal()
    slash_changed     = pyqtSignal(str)        # query after '/'; "" = hide menu

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._attach_path = ""
        self._coding_mode = False
        self._ext_menu: SlashMenu | None = None   # set by FloatingPanel
        self._build_ui()

    def attach_menu(self, menu: 'SlashMenu'):
        """Wire an externally-owned SlashMenu (lives in panel layout)."""
        self._ext_menu = menu
        self._field.key_up.connect(lambda: menu.move_cursor(-1))
        self._field.key_down.connect(lambda: menu.move_cursor(1))
        self._field.key_escape.connect(menu.hide)
        menu.command_selected.connect(self._on_cmd_selected)

    # ── Build ─────────────────────────────────────────────────────────────────

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
                background: rgba(255, 255, 255, 0.05); 
                border: none; 
                border-radius: 17px;
                color: rgba(255, 255, 255, 0.6); 
                font-size: 18px; 
                font-weight: 500;
            }
            QPushButton:hover { 
                background: rgba(255, 255, 255, 0.1); 
                color: #ffffff;
            }
        """)

        self._field = _SlashLineEdit()
        self._field.setPlaceholderText("Message or / for commands…")
        self._field.setFont(QFont("Inter", 10))
        self._field.returnPressed.connect(self._send)
        self._field.textChanged.connect(self._on_text_changed)
        self._field.setStyleSheet("""
            QLineEdit {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 18px; 
                min-height: 36px; 
                max-height: 36px;
                padding: 0 16px; 
                color: #f8fafc;
                selection-background-color: #6366f1;
            }
            QLineEdit:focus {
                border: 1px solid rgba(99, 102, 241, 0.4);
                background: rgba(255, 255, 255, 0.08);
            }
        """)

        self._btn = QPushButton("↑")
        self._btn.setFixedSize(34, 34)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._send)
        self._btn.setStyleSheet(_BTN_SEND)

        layout.addWidget(self._attach_btn)
        layout.addWidget(self._field)
        layout.addWidget(self._btn)

    # ── Slash menu logic ──────────────────────────────────────────────────────

    def _on_text_changed(self, text: str):
        if not self._coding_mode or not self._ext_menu:
            return
        if text.startswith("/"):
            # Emit full text so receiver knows "/" alone means "show all"
            self.slash_changed.emit(text)
        else:
            self.slash_changed.emit("")

    def _on_cmd_selected(self, cmd: str):
        """Called when menu row is clicked or Enter confirms a selection."""
        if self._ext_menu:
            self._ext_menu.hide()
        self.slash_changed.emit("")
        hint = next((arg for name, _, arg in SLASH_COMMANDS if name == cmd), "")
        if hint:
            self._field.setText(cmd + " ")
            self._field.setFocus()
        else:
            self._field.clear()
            self.command_triggered.emit(cmd, "")

    # ── Send / stop ───────────────────────────────────────────────────────────

    def _send(self):
        # Stop mode
        if self._btn.text() == "■":
            if self._ext_menu:
                self._ext_menu.hide()
            self.stopped.emit()
            return

        # Enter on an open menu
        if self._ext_menu and self._ext_menu.isVisible() and self._ext_menu.has_results:
            cmd = self._ext_menu.accept_current()
            if cmd:
                self._on_cmd_selected(cmd)
            return

        text = self._field.text().strip()

        # Slash command typed manually
        if text.startswith("/"):
            parts = text.split(" ", 1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            known = {c[0] for c in SLASH_COMMANDS}
            if cmd in known:
                self._field.clear()
                if self._ext_menu:
                    self._ext_menu.hide()
                self.command_triggered.emit(cmd, arg)
                return

        # ── Normal message ─────────────────────────────────────────────────────
        if text or self._attach_path:
            self._field.clear()
            attached = self._attach_path
            self._attach_path = ""
            self._attach_btn.setText("+")
            if self._ext_menu:
                self._ext_menu.hide()
            self.submitted.emit(text, attached)

    # ── Attachment ────────────────────────────────────────────────────────────

    def _toggle_attachment(self):
        if self._attach_path:
            self._attach_path = ""
            self._attach_btn.setText("+")
            self._field.setPlaceholderText("Message or / for commands…")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Attach File", "",
                "Images & Docs (*.png *.jpg *.jpeg *.pdf *.txt *.csv *.md)"
            )
            if path:
                self._attach_path = path
                self._attach_btn.setText("×")
                fname = os.path.basename(path)
                self._field.setPlaceholderText(f"[{fname[:18]}] Message…")

    # ── State helpers ─────────────────────────────────────────────────────────

    def set_coding_mode(self, is_coding: bool):
        """Enable or disable slash command menu (only shown in Coding mode)."""
        self._coding_mode = is_coding
        if not is_coding and self._ext_menu:
            self._ext_menu.hide()
        placeholder = "/ for commands…" if is_coding else "Type a message…"
        self._field.setPlaceholderText(placeholder)

    def set_generating(self, is_gen: bool):
        if is_gen:
            self._btn.setText("■")
            self._btn.setEnabled(True)
            self._btn.setStyleSheet(_BTN_STOP)
            self._field.setPlaceholderText("Generating… click ■ to stop")
        else:
            self._btn.setText("↑")
            self._btn.setEnabled(True)
            self._btn.setStyleSheet(_BTN_SEND)
            placeholder = "/ for commands…" if self._coding_mode else "Type a message…"
            self._field.setPlaceholderText(placeholder)

    def set_enabled(self, enabled: bool):
        self._field.setEnabled(enabled)
        self._btn.setEnabled(True)   # always keep button live for stop

    def focus(self):
        self._field.setFocus()
