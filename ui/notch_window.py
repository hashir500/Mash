"""NotchWindow — Dynamic Island-style frameless always-on-top Mash window.

States
------
COLLAPSED  : 200×36 pill at top-centre
EXPANDED   : 420×216 card drops down (animation only)
"""
import os
import re
import subprocess
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication,
    QGraphicsOpacityEffect, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QRect, QRectF, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtSignal, QEvent, QPoint
)
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QLinearGradient,
    QRadialGradient, QFont, QFontDatabase, QCursor
)

from ui.character_widget import CharacterWidget
from ui.chat_widget import ChatWidget
from ui.input_bar import InputBar
from ai.openrouter import StreamWorker

from PyQt6.QtWidgets import QHBoxLayout, QPushButton


# ── geometry constants ────────────────────────────────────────────────────
PILL_W, PILL_H    = 200, 36
CARD_W, CARD_H    = 420, 216
CORNER_PILL       = 18
CORNER_CARD       = 36
ANIM_MS           = 380

PANEL_W           = 420


class State(Enum):
    COLLAPSED = auto()
    EXPANDED  = auto()


class ModeSelector(QWidget):
    mode_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 5)
        layout.setSpacing(8)

        self._buttons = {}
        modes = [
            ("✦  General",  "general",   "#a78bfa"),   # purple
            ("⬡  Reasoning", "reasoning", "#34d399"),   # green
            ("⌨  Coding",   "coding",    "#60a5fa"),   # blue
        ]
        
        for label, mode_id, accent in modes:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: rgba(255, 255, 255, 0.38);
                    border: none;
                    border-radius: 0px;
                    font-size: 10px;
                    font-weight: 500;
                    font-family: 'Inter', sans-serif;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    color: rgba(255, 255, 255, 0.75);
                }}
                QPushButton:checked {{
                    color: {accent};
                    border-bottom: 2px solid {accent};
                    background: transparent;
                }}
            """)
            btn.clicked.connect(lambda checked, m=mode_id: self._on_clicked(m))
            layout.addWidget(btn)
            self._buttons[mode_id] = btn

        self._current_mode = "general"
        self._buttons["general"].setChecked(True)
        layout.addStretch()

        # Small cross icon clear button
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedSize(22, 22)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Clear conversation")
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(255, 255, 255, 0.22);
                border: none;
                border-radius: 11px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(255, 80, 80, 0.18);
                color: rgba(255, 100, 100, 0.9);
            }
        """)
        layout.addWidget(self.clear_btn)

    def _on_clicked(self, mode_id):
        self._current_mode = mode_id
        for mid, btn in self._buttons.items():
            btn.setChecked(mid == mode_id)
        self.mode_changed.emit(mode_id)

    def current_mode(self):
        return self._current_mode


class FloatingPanel(QWidget):
    """A detached rounded rectangle that appears below the notch on hover."""
    def __init__(self, parent_window):
        super().__init__()
        self._parent_win = parent_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedWidth(PANEL_W)

        # UI
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # Background container
        self._bg = QWidget(self)
        self._bg.setFixedWidth(PANEL_W)
        self._bg.setStyleSheet(f"""
            QWidget {{
                background: #050505;
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 28px;
            }}
        """)
        bg_layout = QVBoxLayout(self._bg)
        bg_layout.setContentsMargins(8, 8, 8, 8)
        bg_layout.setSpacing(8)
        
        self.chat = ChatWidget()
        self.chat.setStyleSheet("QWidget { border: none; background: transparent; }")
        self.chat.setVisible(False)
        self.chat.content_size_changed.connect(self._on_chat_size_changed)
        
        self.mode_selector = ModeSelector()
        
        self.input = InputBar()
        
        bg_layout.addWidget(self.chat)
        bg_layout.addWidget(self.mode_selector)
        bg_layout.addWidget(self.input)
        layout.addWidget(self._bg)

        # Opacity animation
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)

        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def _on_chat_size_changed(self, content_w, new_h):
        # Base width is 420. If content (e.g. table) is wider, expand up to 900px.
        new_w = max(420, min(content_w + 40, 900))
        if self.width() != new_w:
            self._bg.setFixedWidth(new_w)
            self.setFixedWidth(new_w)
            self.align_to_parent()

    def align_to_parent(self):
        pr = self._parent_win.geometry()
        x = pr.x() + (pr.width() - self.width()) // 2
        y = pr.y() + pr.height() + 16  # Increased gap to 16px to prevent overlap
        self.move(x, y)

    def show_animated(self):
        self.align_to_parent()
        self.setVisible(True)
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self):
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()
        # when anim finishes, it just stays invisible (opacity 0)


class NotchWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._state         = State.COLLAPSED
        self._animating     = False
        self._expanding     = False
        self._pulse         = 0.0
        self._pulse_dir     = 1
        self._drag_pos      = QPoint()
        self._worker: StreamWorker | None = None
        self._history: list[dict] = []
        
        self._api_key = os.getenv("OPENROUTER_API_KEY", "")

        self._setup_window()
        self._load_fonts()
        self._build_ui()
        self._setup_animations()
        self._position_collapsed()

        # Detached floating panel
        self._panel = FloatingPanel(self)
        self._panel.input.submitted.connect(self._on_submit)
        self._panel.mode_selector.clear_btn.clicked.connect(self._clear_history)
        self._panel.mode_selector.mode_changed.connect(self._char.set_mode)
        self._panel.mode_selector.mode_changed.connect(self._on_mode_changed)
        
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start(35)

        self._raise_timer = QTimer(self)
        self._raise_timer.timeout.connect(self._ensure_on_top)
        self._raise_timer.start(500)

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

    def _load_fonts(self):
        QFontDatabase.addApplicationFont("/usr/share/fonts/truetype/inter/Inter-Regular.ttf")

    def _build_ui(self):
        self._content = QWidget(self)
        self._content.setGeometry(0, 0, CARD_W, CARD_H)
        self._content.setVisible(False)
        self._content.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(8, 8, 8, 8)  # Thin even bezels on all sides
        layout.setSpacing(0)

        self._char = CharacterWidget()
        layout.addWidget(self._char)

    def _setup_animations(self):
        self._geo_anim = QPropertyAnimation(self, b"geometry")
        self._geo_anim.setDuration(ANIM_MS)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._geo_anim.finished.connect(self._on_anim_done)

    def _screen_rect(self):
        screen = self.screen()
        if screen:
            return screen.geometry()
        return QApplication.primaryScreen().geometry()

    def _collapsed_rect(self):
        sr = self._screen_rect()
        cx = sr.x() + sr.width() // 2
        return QRect(cx - PILL_W // 2, sr.y(), PILL_W, PILL_H)

    def _expanded_rect(self):
        sr = self._screen_rect()
        cx = sr.x() + sr.width() // 2
        return QRect(cx - CARD_W // 2, sr.y(), CARD_W, CARD_H)

    def _position_collapsed(self):
        self.setGeometry(self._collapsed_rect())

    def _ensure_on_top(self):
        if self.isVisible():
            self.raise_()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            
        if self._panel.isVisible():
            self._panel.raise_()
            self._panel.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self._panel.show()

    def expand(self):
        if self._state == State.EXPANDED or self._animating:
            return
        self._expanding = True
        self._animating = True
        self._content.setVisible(True)
        self._content.setGeometry(0, 0, CARD_W, CARD_H)
        self._geo_anim.setStartValue(self.geometry())
        self._geo_anim.setEndValue(self._expanded_rect())
        self._geo_anim.start()

    def collapse(self):
        if self._state == State.COLLAPSED or self._animating:
            return
        self._expanding = False
        self._animating = True
        
        # 1. Fade out the text panel
        self._panel.hide_animated()
        
        # 2. Wait 200ms, then trigger the bouncy notch shrink
        QTimer.singleShot(200, self._start_collapse_anim)

    def _start_collapse_anim(self):
        self._geo_anim.setStartValue(self.geometry())
        self._geo_anim.setEndValue(self._collapsed_rect())
        self._geo_anim.start()
        
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.show()

    def _on_anim_done(self):
        self._animating = False
        if self._expanding:
            self._state = State.EXPANDED
        else:
            self._state = State.COLLAPSED
            self._content.setVisible(False)
            self._panel.hide()
        self.update()

    def _maybe_collapse(self):
        if self._state == State.EXPANDED and not self._animating:
            self.collapse()
            QTimer.singleShot(ANIM_MS + 50, self._ensure_on_top)

    _CODING_SYSTEM_PROMPT = (
        "You are an autonomous coding agent. When asked to build something, "
        "output ONLY the files and a summary. Use this exact format for each file:\n"
        "===FILE: filename.ext===\n<full file content>\n===END===\n"
        "After all files, write:\n"
        "===SUMMARY===\n"
        "A concise description: what was built, files created, how to run it.\n"
        "===END===\n"
        "Do NOT write any explanation outside the FILE and SUMMARY blocks."
    )

    def _on_mode_changed(self, mode: str):
        # Clear the injected system prompt when leaving coding mode
        self._coding_system_injected = False

    def _on_submit(self, text: str, attachment: str = ""):
        if not self._api_key:
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⚠️  No API key found. Set OPENROUTER_API_KEY in .env")
            self._panel.chat.finalize_assistant_message()
            return

        self._panel.chat.setVisible(True)
        # If no text but attached file, show a small indicator in chat
        display_text = text if text else f"[File Attached]"
        self._panel.chat.add_user_message(display_text)
        
        self._history.append({"role": "user", "content": text})
        self._panel.input.set_enabled(False)
        self._char.set_thinking(True)
        self._panel.chat.start_assistant_message()

        mode = self._panel.mode_selector.current_mode()
        model_id = StreamWorker.get_model_for_mode(mode, self._api_key)

        # Build message list — inject system prompt for coding mode
        messages = list(self._history)
        if mode == "coding":
            messages = [{"role": "system", "content": self._CODING_SYSTEM_PROMPT}] + messages
            self._coding_raw_buffer = ""  # reset buffer for this turn

        self._worker = StreamWorker(
            messages,
            self._api_key, 
            attachment_path=attachment, 
            model_id=model_id,
            parent=self
        )
        self._worker.token_received.connect(self._on_token)
        self._worker.reasoning_received.connect(self._on_reasoning)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_token(self, token: str):
        mode = self._panel.mode_selector.current_mode()
        if mode == "coding":
            self._coding_raw_buffer += token
            self._parse_coding_stream_live()
            if self._char._is_thinking:
                self._char.set_thinking(False)
                self._char.set_writing(True)
        else:
            self._panel.chat.append_token(token)
            if self._char._is_thinking:
                self._char.set_thinking(False)
                self._char.set_writing(True)

    def _parse_coding_stream_live(self):
        """Called on every token in coding mode — detect and write files in real-time."""
        buf = self._coding_raw_buffer

        # Detect FILE header being opened: ===FILE: name.ext===
        if not hasattr(self, "_coding_current_file"):
            self._coding_current_file = None
            self._coding_file_buf = ""
            self._coding_workspace = None
            self._coding_vscode_opened = False
            self._coding_live_bubble_started = False
            self._coding_files_written = []

        if self._coding_current_file is None:
            # Look for ===FILE: filename===
            header_match = re.search(r'===FILE:\s*(.+?)===\n', buf)
            if header_match:
                filename = header_match.group(1).strip()
                self._coding_current_file = filename
                self._coding_file_buf = ""
                # Consume everything up to (and including) the header
                self._coding_raw_buffer = buf[header_match.end():]
                # Start live display
                if not self._coding_live_bubble_started:
                    self._coding_live_bubble_started = True
                    self._panel.chat.start_assistant_message()
                self._panel.chat.append_token(f"📝 Writing `{filename}`...")
                # Create workspace
                if self._coding_workspace is None:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    self._coding_workspace = os.path.expanduser(
                        f"~/MashWorkspace/session_{timestamp}"
                    )
                    os.makedirs(self._coding_workspace, exist_ok=True)
        else:
            # We're inside a file block — look for ===END===
            end_idx = buf.find("===END===")
            if end_idx != -1:
                # Complete file content found
                file_content = buf[:end_idx].strip()
                filepath = os.path.join(self._coding_workspace, self._coding_current_file)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(file_content)
                self._coding_files_written.append(self._coding_current_file)
                self._panel.chat.append_token(f" ✅\n")

                # Open VS Code on first file
                if not self._coding_vscode_opened:
                    try:
                        subprocess.Popen(["code", self._coding_workspace])
                        self._coding_vscode_opened = True
                    except FileNotFoundError:
                        pass

                # Reset for next file
                self._coding_current_file = None
                self._coding_file_buf = ""
                self._coding_raw_buffer = buf[end_idx + len("===END==="):]


    def _on_reasoning(self, text: str):
        mode = self._panel.mode_selector.current_mode()
        if mode != "general":
            self._panel.chat.append_reasoning(text)
        
        # Still show thinking animation while it's reasoning
        if not self._char._is_thinking and not self._char._is_writing:
            self._char.set_thinking(True)

    def _on_done(self):
        mode = self._panel.mode_selector.current_mode()

        if mode == "coding":
            raw = getattr(self, "_coding_raw_buffer", "")
            self._history.append({"role": "assistant", "content": raw})

            # Extract summary block if present
            summary_match = re.search(r'===SUMMARY===\n(.*?)===END===', raw, re.DOTALL)
            summary = summary_match.group(1).strip() if summary_match else None
            files_written = getattr(self, "_coding_files_written", [])
            workspace = getattr(self, "_coding_workspace", None)

            # Show final summary card
            if files_written or summary:
                files_list = "".join(f"<li><code>{f}</code></li>" for f in files_written)
                loc_line = f"<code>{workspace}</code>" if workspace else "~/MashWorkspace"
                card = f"""<div style='margin-top:8px; line-height:1.7;'>
  <hr style='border:none;border-top:1px solid rgba(255,255,255,0.1);margin:6px 0;'/>
  <div style='color:#60a5fa; font-weight:bold;'>⌨ Build Complete — {len(files_written)} file(s) in {loc_line}</div>
  {'<ul style="margin:4px 0 8px 0; padding-left:18px;">' + files_list + '</ul>' if files_list else ''}
  {'<b>What was built:</b><br/>' + summary if summary else ''}
</div>"""
                self._panel.chat.append_token(card)

            self._panel.chat.finalize_assistant_message()
            self._panel.input.set_enabled(True)
            self._char.set_thinking(False)
            self._char.set_writing(False)
            self._worker = None

            # Reset live coding state for next turn
            self._coding_raw_buffer = ""
            self._coding_current_file = None
            self._coding_file_buf = ""
            self._coding_workspace = None
            self._coding_vscode_opened = False
            self._coding_live_bubble_started = False
            self._coding_files_written = []

        else:
            # Normal modes
            if self._panel.chat._current_bubble:
                final_text = self._panel.chat._current_bubble._text
                if final_text:
                    self._history.append({"role": "assistant", "content": final_text})
            self._panel.chat.finalize_assistant_message()
            self._panel.input.set_enabled(True)
            self._char.set_thinking(False)
            self._char.set_writing(False)
            self._worker = None

    def _process_coding_output(self, raw: str):
        """Parse ===FILE=== blocks, write files, open VS Code, show summary in chat."""
        # Parse structured FILE blocks: ===FILE: name.ext===\n...content...\n===END===
        file_pattern = re.compile(
            r'===FILE:\s*(.+?)===\n(.*?)===END===', re.DOTALL
        )
        summary_pattern = re.compile(
            r'===SUMMARY===\n(.*?)===END===', re.DOTALL
        )

        file_matches = file_pattern.findall(raw)
        summary_match = summary_pattern.search(raw)

        # Fallback: also try markdown code blocks if structured format not used
        if not file_matches:
            md_pattern = re.compile(r'```([\w+#-]*)\n(.*?)```', re.DOTALL)
            ext_map = {
                "python": "py", "py": "py", "cpp": "cpp", "c++": "cpp", "c": "c",
                "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
                "html": "html", "css": "css", "java": "java", "rust": "rs",
                "go": "go", "bash": "sh", "sh": "sh", "shell": "sh",
                "sql": "sql", "json": "json", "yaml": "yaml", "yml": "yaml",
            }
            md_matches = md_pattern.findall(raw)
            if md_matches:
                file_matches = [
                    (f"main.{ext_map.get(lang.strip().lower(), 'txt')}", code)
                    for i, (lang, code) in enumerate(md_matches)
                ]
                # number duplicates
                seen = {}
                numbered = []
                for name, code in file_matches:
                    if name in seen:
                        seen[name] += 1
                        base, ext = name.rsplit(".", 1)
                        name = f"{base}_{seen[name]}.{ext}"
                    else:
                        seen[name] = 0
                    numbered.append((name, code))
                file_matches = numbered

        if not file_matches:
            # Nothing to write
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⚠️ No code files detected in the response.")
            self._panel.chat.finalize_assistant_message()
            return

        # Create timestamped workspace
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = os.path.expanduser(f"~/MashWorkspace/session_{timestamp}")
        os.makedirs(workspace, exist_ok=True)

        created_files = []
        for filename, code in file_matches:
            filename = filename.strip()
            filepath = os.path.join(workspace, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code.strip())
            created_files.append(filename)

        # Open VS Code
        vscode_ok = False
        try:
            subprocess.Popen(["code", workspace])
            vscode_ok = True
        except FileNotFoundError:
            pass

        # Build the summary message to show in chat
        summary = summary_match.group(1).strip() if summary_match else None

        files_list = "".join(f"<li><code>{f}</code></li>" for f in created_files)
        vscode_line = "✅ Opened in VS Code." if vscode_ok else f"📁 Saved to <code>{workspace}</code>"

        chat_summary = f"""
<div style='line-height:1.7;'>
  <div style='color:#60a5fa; font-weight:bold; font-size:11pt; margin-bottom:6px;'>⌨ Build Complete</div>
  <b>Files created:</b>
  <ul style='margin:4px 0 8px 0; padding-left:18px;'>{files_list}</ul>
  <div style='margin-bottom:4px;'>{vscode_line}</div>
  {'<hr style="border:none;border-top:1px solid rgba(255,255,255,0.1);margin:8px 0;"/><b>Summary</b><br/>' + summary if summary else ''}
</div>
"""
        self._panel.chat.start_assistant_message()
        self._panel.chat.append_token(chat_summary)
        self._panel.chat.finalize_assistant_message()

    def _open_in_vscode(self, text: str):
        """Legacy fallback — kept for compatibility."""
        # Find all fenced code blocks: ```lang\n...code...\n```
        ext_map = {
            "python": "py", "py": "py", "cpp": "cpp", "c++": "cpp", "c": "c",
            "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
            "html": "html", "css": "css", "java": "java", "rust": "rs",
            "go": "go", "bash": "sh", "sh": "sh", "shell": "sh",
            "sql": "sql", "json": "json", "yaml": "yaml", "yml": "yaml",
        }
        pattern = re.compile(r'```([\w+#-]*)\n(.*?)```', re.DOTALL)
        matches = pattern.findall(text)

        if not matches:
            return  # No code blocks found, do nothing

        # Create timestamped workspace folder
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = os.path.expanduser(f"~/MashWorkspace/session_{timestamp}")
        os.makedirs(workspace, exist_ok=True)

        file_count = 0
        for i, (lang, code) in enumerate(matches):
            lang = lang.strip().lower()
            ext = ext_map.get(lang, "txt")
            filename = f"code_{i + 1}.{ext}" if len(matches) > 1 else f"main.{ext}"
            filepath = os.path.join(workspace, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code.strip())
            file_count += 1

        # Open VS Code
        try:
            subprocess.Popen(["code", workspace])
            self._panel.chat.add_user_message(
                f"📂 <i>Saved {file_count} file(s) to <b>~/MashWorkspace/session_{timestamp}</b> and opened VS Code.</i>"
            )
        except FileNotFoundError:
            self._panel.chat.add_user_message(
                f"📂 <i>Saved {file_count} file(s) to <b>{workspace}</b>. (VS Code not found in PATH)</i>"
            )

    def _clear_history(self):
        self._history = []
        self._panel.chat.add_user_message("✨ <i>History cleared.</i>")

    def _on_error(self, msg: str):
        self._panel.chat.append_token(f"\n\n⚠️  Error: {msg}")
        self._panel.chat.finalize_assistant_message()
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._char.set_writing(False)
        self._worker = None

    def _tick_pulse(self):
        self._pulse += 0.06 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse = 1.0
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse = 0.0
            self._pulse_dir = 1
        if self._state == State.COLLAPSED:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._state == State.COLLAPSED or self._animating:
            w, h = self.width(), self.height()
            frac = (h - PILL_H) / max(1, CARD_H - PILL_H)
            frac = max(0.0, min(1.0, frac))
            corner = CORNER_PILL + (CORNER_CARD - CORNER_PILL) * frac
        else:
            w, h = self.width(), self.height()
            frac  = 1.0
            corner = CORNER_CARD

        rect = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, corner, corner)

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(8, 8, 8))
        bg.setColorAt(1, QColor(0, 0, 0))
        p.fillPath(path, bg)

        glow_pen = QPen(QColor(255, 255, 255, int(10 + 5 * self._pulse)), 3)
        p.setPen(glow_pen)
        outer = QPainterPath()
        outer.addRoundedRect(rect.adjusted(-1, -1, 1, 1), corner + 1, corner + 1)
        p.drawPath(outer)

        border_pen = QPen(QColor(255, 255, 255, int(20 + 20 * self._pulse)), 1)
        p.setPen(border_pen)
        p.drawPath(path)

        if self._state == State.COLLAPSED:
            self._paint_pill_content(p, w, h)

    def _paint_pill_content(self, p, w, h):
        p.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        alpha = int(180 + 60 * self._pulse)
        p.setPen(QColor(255, 255, 255, alpha))
        p.drawText(QRect(0, 0, w - 28, h), Qt.AlignmentFlag.AlignCenter, "Mash")

        dot_x = w - 22
        dot_y = h // 2
        dot_r = 5 + self._pulse * 2.5

        rg = QRadialGradient(dot_x, dot_y, dot_r * 2.2)
        rg.setColorAt(0, QColor(255, 255, 255, int(40 * self._pulse)))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(
            int(dot_x - dot_r * 2.5), int(dot_y - dot_r * 2.5),
            int(dot_r * 5), int(dot_r * 5), rg
        )
        p.setBrush(QColor(255, 255, 255, int(200 + 55 * self._pulse)))
        p.drawEllipse(QRectF(dot_x - dot_r / 2, dot_y - dot_r / 2, dot_r, dot_r))

    def _paint_pill_label(self, p, w):
        p.setFont(QFont("Inter", 11, QFont.Weight.Medium))
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRect(0, 0, w, PILL_H), Qt.AlignmentFlag.AlignCenter, "Mash")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self._state == State.COLLAPSED:
                self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                self.show()
                self.expand()
            elif self._state == State.EXPANDED:
                # Left-click the expanded notch to reveal the text panel
                if not self._panel.isVisible() or self._panel._fx.opacity() == 0.0:
                    self._panel.show_animated()
                    self._panel.input.focus()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._state == State.EXPANDED:
                self.collapse()
            else:
                self.close()

    def mouseMoveEvent(self, event):
        if (event.buttons() == Qt.MouseButton.LeftButton
                and self._state == State.COLLAPSED):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._state == State.EXPANDED:
            self.collapse()
        super().keyPressEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and not self._panel.isActiveWindow() and self._state == State.EXPANDED:
                QTimer.singleShot(50, self._maybe_collapse)
        super().changeEvent(event)

    def closeEvent(self, event):
        self._raise_timer.stop()
        self._panel.close()
        if self._worker:
            self._worker.abort()
            self._worker.wait(2000)
        super().closeEvent(event)
