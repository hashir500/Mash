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
    QEasingCurve, pyqtSignal, QEvent, QPoint, QThread
)
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QLinearGradient,
    QRadialGradient, QFont, QFontDatabase, QCursor
)

from ui.character_widget import CharacterWidget
from ui.chat_widget import ChatWidget
from ui.input_bar import InputBar, SlashMenu
from ai.openrouter import StreamWorker
from ai.agentic_worker import AgenticCodingWorker
import utils.projects as project_registry

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

        # Slash menu — embedded in panel layout, above input bar
        self.slash_menu = SlashMenu()
        self.slash_menu.setVisible(False)
        self.slash_menu.setStyleSheet("""
            QFrame {
                background: #0d0f14;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
        """)

        self.input = InputBar()
        self.input.attach_menu(self.slash_menu)
        self.input.slash_changed.connect(self._on_slash_query)
        
        bg_layout.addWidget(self.chat)
        bg_layout.addWidget(self.mode_selector)
        bg_layout.addWidget(self.slash_menu)
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

    def _on_slash_query(self, text: str):
        """Show/filter/hide the embedded slash menu."""
        if not text:
            self.slash_menu.setVisible(False)
            return
        query = text[1:] if text.startswith("/") else text  # strip leading /
        self.slash_menu.filter(query)
        self.slash_menu.setVisible(self.slash_menu.has_results)

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


class _CommandRunner(QThread):
    """Runs a shell command in a background thread and emits output line-by-line."""
    output_line = pyqtSignal(str)
    done        = pyqtSignal(int)   # exit code

    def __init__(self, cmd: str, cwd: str, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None

    def kill(self):
        """Hard-kill the subprocess and its process group."""
        import os, signal
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def run(self):
        import shlex
        try:
            self.proc = subprocess.Popen(
                shlex.split(self.cmd),
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,   # gives it its own process group
            )
            for line in self.proc.stdout:
                self.output_line.emit(line)
            self.proc.wait()
            self.done.emit(self.proc.returncode)
        except Exception as e:
            self.output_line.emit(f"❌ Failed: {e}\n")
            self.done.emit(1)


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
        self._agentic_worker: AgenticCodingWorker | None = None
        self._history: list[dict] = []
        
        self._api_key = os.getenv("OPENROUTER_API_KEY", "")

        # Auto-discover any pre-existing projects in ~/MashProjects/
        project_registry.scan_existing()

        self._setup_window()
        self._load_fonts()
        self._build_ui()
        self._setup_animations()
        self._position_collapsed()

        # Detached floating panel
        self._panel = FloatingPanel(self)
        self._panel.input.submitted.connect(self._on_submit)
        self._panel.input.stopped.connect(self._on_stop)
        self._panel.input.command_triggered.connect(self._on_command)
        self._panel.mode_selector.clear_btn.clicked.connect(self._clear_history)
        self._panel.mode_selector.mode_changed.connect(self._char.set_mode)
        self._panel.mode_selector.mode_changed.connect(self._on_mode_changed)
        self._panel.mode_selector.mode_changed.connect(
            lambda m: self._panel.input.set_coding_mode(m == "coding")
        )
        
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

    def _on_stop(self):
        """Interrupt AI generation AND kill any running project process."""
        # Kill the tracked project run process (server/app)
        run_runner = getattr(self, "_run_runner", None)
        if run_runner and run_runner.isRunning():
            run_runner.kill()
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⏹ Project process killed.")
            self._panel.chat.finalize_assistant_message()
            self._run_runner = None
        # Abort agentic worker
        worker = getattr(self, "_agentic_worker", None)
        if worker and worker.isRunning():
            worker.abort()
        # Abort stream worker
        sw = getattr(self, "_worker", None)
        if sw and sw.isRunning():
            sw.abort()
        # Kill any other running command runners
        for runner in getattr(self, "_cmd_runners", []):
            if runner.isRunning():
                runner.kill()
        self._panel.input.set_generating(False)
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._char.set_writing(False)
        # Only append Stopped if there's an open AI bubble
        if self._panel.chat._current_bubble:
            self._panel.chat.append_token("\n⏹ Stopped.")
            self._panel.chat.finalize_assistant_message()

    # ── Slash command dispatcher ──────────────────────────────────────────────

    def _on_command(self, cmd: str, arg: str):
        """Route /command arg to the correct handler."""
        handlers = {
            "/stop":         lambda: self._on_stop(),
            "/projects":     lambda: self._cmd_projects(),
            "/clear":        lambda: self._clear_history(),
            "/run":          lambda: self._cmd_run(),
            "/requirements": lambda: self._cmd_requirements(),
            "/switch":       lambda: self._cmd_switch(arg),
            "/open":         lambda: self._cmd_open_vscode(),
            "/git":          lambda: self._cmd_git(),
            "/code":         lambda: self._cmd_code(arg),
            "/debug":        lambda: self._cmd_debug(arg),
            "/chat":         lambda: self._cmd_chat(arg),
            "/explain":      lambda: self._cmd_explain(arg),
        }
        fn = handlers.get(cmd)
        if fn:
            fn()
        else:
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token(f"⚠️ Unknown command: `{cmd}`")
            self._panel.chat.finalize_assistant_message()

    # ── Individual command handlers ───────────────────────────────────────────

    def _cmd_projects(self):
        self._panel.chat.setVisible(True)
        all_projects = project_registry.load_all()
        self._panel.chat.start_assistant_message()
        if not all_projects:
            self._panel.chat.append_token("No projects yet. Use `/code` to build one!")
        else:
            rows = "".join(
                f"<tr><td><code>{p['name']}</code></td>"
                f"<td style='color:rgba(255,255,255,0.4);font-size:9pt;'>{p.get('created','')}</td>"
                f"<td style='color:rgba(255,255,255,0.4);font-size:9pt;'>{p.get('path','')}</td></tr>"
                for p in all_projects
            )
            self._panel.chat.append_token(
                f"<b>📁 MashProjects ({len(all_projects)}):</b>"
                f"<table style='margin-top:6px;'>{rows}</table>"
                f"<br/><i style='font-size:9pt;color:rgba(255,255,255,0.4);'>"
                f"Use <code>/switch &lt;name&gt;</code> to switch project.</i>"
            )
        self._panel.chat.finalize_assistant_message()

    def _cmd_switch(self, name: str):
        self._panel.chat.setVisible(True)
        if not name:
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("Usage: `/switch <project name>`")
            self._panel.chat.finalize_assistant_message()
            return
        found = project_registry.find(name.strip())
        self._panel.chat.start_assistant_message()
        if found and os.path.exists(found["path"]):
            self._last_agentic_workspace = found["path"]
            self._panel.chat.append_token(
                f"✅ Switched to <b>{found['name']}</b><br/>"
                f"<code>{found['path']}</code><br/>"
                f"<i style='font-size:9pt;'>Use <code>/run</code>, <code>/requirements</code>, or <code>/debug</code>.</i>"
            )
        else:
            self._panel.chat.append_token(f"❌ Project not found: `{name}`. Use `/projects` to list.")
        self._panel.chat.finalize_assistant_message()

    def _cmd_open_vscode(self):
        self._panel.chat.setVisible(True)
        ws = getattr(self, "_last_agentic_workspace", None)
        self._panel.chat.start_assistant_message()
        if ws and os.path.exists(ws):
            try:
                subprocess.Popen(["code", ws])
                self._panel.chat.append_token(f"📂 Opened <code>{ws}</code> in VS Code.")
            except FileNotFoundError:
                self._panel.chat.append_token("❌ VS Code (`code`) not found in PATH.")
        else:
            self._panel.chat.append_token("❌ No active project. Use `/switch <name>` first.")
        self._panel.chat.finalize_assistant_message()

    def _cmd_run(self):
        ws = getattr(self, "_last_agentic_workspace", None)
        self._panel.chat.setVisible(True)
        if not ws or not os.path.exists(ws):
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("❌ No active project. Use `/switch <name>` first.")
            self._panel.chat.finalize_assistant_message()
            return
        cmds = self._resolve_commands("run", ws)
        if not cmds:
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⚠️ Couldn't determine how to run this project.")
            self._panel.chat.finalize_assistant_message()
            return
        for cmd in cmds:
            self._run_command_in_chat(cmd, ws, track_as_run=True)

    def _cmd_requirements(self):
        ws = getattr(self, "_last_agentic_workspace", None)
        self._panel.chat.setVisible(True)
        if not ws or not os.path.exists(ws):
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("❌ No active project. Use `/switch <name>` first.")
            self._panel.chat.finalize_assistant_message()
            return
        cmds = self._resolve_commands("install dependencies", ws)
        if not cmds:
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⚠️ No recognised dependency files found in the project.")
            self._panel.chat.finalize_assistant_message()
            return
        for cmd in cmds:
            self._run_command_in_chat(cmd, ws)

    def _cmd_git(self):
        ws = getattr(self, "_last_agentic_workspace", None)
        self._panel.chat.setVisible(True)
        if not ws or not os.path.exists(ws):
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("❌ No active project. Use `/switch <name>` first.")
            self._panel.chat.finalize_assistant_message()
            return
        self._run_command_in_chat("git status && git diff --stat", ws)

    def _cmd_code(self, prompt: str):
        if not prompt:
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("Usage: `/code <describe what to build>`")
            self._panel.chat.finalize_assistant_message()
            return
        # Force coding mode and submit
        self._panel.mode_selector._on_clicked("coding")
        self._on_submit(prompt)

    def _cmd_debug(self, prompt: str):
        if not prompt:
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("Usage: `/debug <describe the error or problem>`")
            self._panel.chat.finalize_assistant_message()
            return
        ws = getattr(self, "_last_agentic_workspace", None)
        debug_prompt = (
            f"Debug and fix this issue in my project"
            f"{f' at {ws}' if ws else ''}: {prompt}"
        )
        self._panel.mode_selector._on_clicked("coding")
        self._on_submit(debug_prompt)

    def _cmd_chat(self, msg: str):
        if not msg:
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("Usage: `/chat <your message>`")
            self._panel.chat.finalize_assistant_message()
            return
        self._panel.mode_selector._on_clicked("general")
        self._on_submit(msg)

    def _cmd_explain(self, topic: str):
        if not topic:
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("Usage: `/explain <topic, concept, or paste code>`")
            self._panel.chat.finalize_assistant_message()
            return
        self._panel.mode_selector._on_clicked("general")
        self._on_submit(f"Explain this clearly and concisely: {topic}")

    def _on_submit(self, text: str, attachment: str = ""):
        if not self._api_key:
            self._panel.chat.setVisible(True)
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
        self._panel.input.set_generating(True)
        self._char.set_thinking(True)
        self._panel.chat.start_assistant_message()

        mode = self._panel.mode_selector.current_mode()
        model_id = StreamWorker.get_model_for_mode(mode, self._api_key)

        if mode == "coding":
            # ── Detect special commands first ─────────────────────────────────
            _run_keywords = ["install ", "run ", "execute ", "start ", "pip ", "npm ",
                             "python ", "node ", "make ", "cargo ", "go run"]
            _switch_keywords = ["continue ", "open ", "use ", "switch to ", "go to ",
                                "work on ", "add to ", "update "]
            _last_workspace = getattr(self, "_last_agentic_workspace", None)
            text_lower = text.lower()

            # ── Kill port (port conflict recovery) ────────────────────────────
            if "kill port" in text_lower or "free port" in text_lower:
                import re as _re
                m = _re.search(r'(\d{2,5})', text_lower)
                port = m.group(1) if m else getattr(self, "_port_conflict_port", "5000")
                cwd  = getattr(self, "_port_conflict_cwd", _last_workspace or os.path.expanduser("~"))
                retry = getattr(self, "_port_conflict_cmd", None)
                self._panel.chat.finalize_assistant_message()
                self._panel.input.set_enabled(True)
                self._char.set_thinking(False)
                from PyQt6.QtWidgets import QMessageBox
                box = QMessageBox(self)
                box.setWindowTitle("Free port")
                box.setText(f"<b>Kill process on port {port}?</b><br/><br/>"
                             f"<code>fuser -k {port}/tcp</code>")
                box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                box.setDefaultButton(QMessageBox.StandardButton.Yes)
                if box.exec() == QMessageBox.StandardButton.Yes:
                    self._run_command_in_chat(f"fuser -k {port}/tcp", cwd)
                    if retry:
                        import time; time.sleep(1.5)
                        self._run_command_in_chat(retry, cwd)
                        self._port_conflict_cmd = None
                return

            # ── List projects ─────────────────────────────────────────────────
            if any(kw in text_lower for kw in ["list projects", "show projects", "my projects"]):
                self._panel.chat.finalize_assistant_message()
                self._panel.input.set_enabled(True)
                self._char.set_thinking(False)
                all_projects = project_registry.load_all()
                if not all_projects:
                    self._panel.chat.start_assistant_message()
                    self._panel.chat.append_token("No projects built yet. Ask me to build something!")
                    self._panel.chat.finalize_assistant_message()
                else:
                    rows = "".join(
                        f"<tr><td><code>{p['name']}</code></td>"
                        f"<td style='color:rgba(255,255,255,0.4);font-size:9pt;'>{p.get('created','')}</td></tr>"
                        for p in all_projects
                    )
                    self._panel.chat.start_assistant_message()
                    self._panel.chat.append_token(
                        f"<b>📁 Your MashProjects ({len(all_projects)}):</b>"
                        f"<table style='margin-top:6px;border-collapse:collapse;'>{rows}</table>"
                        f"<br/><i style='font-size:9pt;color:rgba(255,255,255,0.4);'>"
                        f"Say 'continue [name]' to switch to a project.</i>"
                    )
                    self._panel.chat.finalize_assistant_message()
                return

            # ── Switch project by name ────────────────────────────────────────
            if any(kw in text_lower for kw in _switch_keywords):
                # Strip the verb and try to find the project
                query = text_lower
                for kw in _switch_keywords:
                    query = query.replace(kw, " ")
                found = project_registry.find(query.strip())
                if found and os.path.exists(found["path"]):
                    self._last_agentic_workspace = found["path"]
                    self._panel.chat.finalize_assistant_message()
                    self._panel.input.set_enabled(True)
                    self._char.set_thinking(False)
                    self._panel.chat.start_assistant_message()
                    self._panel.chat.append_token(
                        f"✅ Switched to <b>{found['name']}</b><br/>"
                        f"<code>{found['path']}</code><br/>"
                        f"<i style='font-size:9pt;'>You can now run commands or ask me to add features.</i>"
                    )
                    self._panel.chat.finalize_assistant_message()
                    return

            # ── Run/install against current workspace ─────────────────────────
            if _last_workspace and any(kw in text_lower for kw in _run_keywords):
                self._panel.chat.finalize_assistant_message()
                self._panel.input.set_enabled(True)
                self._char.set_thinking(False)
                from PyQt6.QtWidgets import QMessageBox
                # Resolve natural language to real shell commands
                resolved = self._resolve_commands(text, _last_workspace)
                if not resolved:
                    self._panel.chat.start_assistant_message()
                    self._panel.chat.append_token(f"⚠️ Couldn't determine commands for: \"{text}\"")
                    self._panel.chat.finalize_assistant_message()
                    return
                for cmd in resolved:
                    box = QMessageBox(self)
                    box.setWindowTitle("Mash wants to run a command")
                    box.setText(f"<b>Allow Mash to run this?</b><br/><br/><code>{cmd}</code>")
                    box.setInformativeText(f"Working directory: {_last_workspace}")
                    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    box.setDefaultButton(QMessageBox.StandardButton.Yes)
                    box.setIcon(QMessageBox.Icon.Question)
                    if box.exec() == QMessageBox.StandardButton.Yes:
                        self._run_command_in_chat(cmd, _last_workspace)
                return

            # ── Agentic multi-step build loop ──────────────────────────────────
            self._coding_raw_buffer = ""
            self._coding_current_file = None
            self._coding_file_buf = ""
            self._coding_workspace = None
            self._coding_vscode_opened = False
            self._coding_live_bubble_started = False
            self._coding_files_written = []

            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("🗂 Planning project structure...\n")


            self._agentic_worker = AgenticCodingWorker(
                request=text,
                api_key=self._api_key,
                model_id=model_id,
                parent=self,
            )
            self._agentic_worker.plan_ready.connect(self._on_plan_ready)
            self._agentic_worker.file_started.connect(self._on_file_started)
            self._agentic_worker.file_done.connect(self._on_file_done)
            self._agentic_worker.build_complete.connect(self._on_build_complete)
            self._agentic_worker.commands_suggested.connect(self._on_commands_suggested)
            self._agentic_worker.error.connect(self._on_agentic_error)
            self._agentic_worker.finished.connect(self._on_agentic_finished)
            self._agentic_worker.start()
        else:
            # ── Standard streaming (General / Reasoning) ───────────────────
            messages = list(self._history)
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

    # ── Agentic coding signals ────────────────────────────────────────────────

    def _on_plan_ready(self, plan: list, project_name: str):
        count = len(plan)
        self._panel.chat.append_token(
            f"✅ Plan ready — building <b>{project_name}</b> ({count} file(s))\n\n"
        )

    def _on_file_started(self, filename: str):
        self._panel.chat.append_token(f"📝 Writing `{filename}`...")

    def _on_file_done(self, filename: str, filepath: str):
        self._panel.chat.append_token(" ✅\n")
        # Open VS Code on first file saved
        if not getattr(self, "_agentic_vscode_opened", False):
            workspace = os.path.dirname(filepath)
            try:
                subprocess.Popen(["code", workspace])
                self._agentic_vscode_opened = True
            except FileNotFoundError:
                pass

    def _on_build_complete(self, workspace: str, files: list):
        self._last_agentic_workspace = workspace
        # Strip timestamp suffix (e.g. flask-rest-api_20260508_122537 → flask-rest-api)
        base = os.path.basename(workspace)
        parts = base.rsplit("_", 2)
        project_name = parts[0] if len(parts) == 3 else base
        project_registry.save(project_name, workspace)
        files_list = "".join(f"<li><code>{f}</code></li>" for f in files)
        card = f"""
<div style='margin-top:10px; line-height:1.7;'>
  <hr style='border:none;border-top:1px solid rgba(255,255,255,0.1);margin:6px 0;'/>
  <div style='color:#60a5fa; font-weight:bold; font-size:11pt;'>⌨ Build Complete — {len(files)} file(s)</div>
  <div style='color:rgba(255,255,255,0.5); font-size:9pt;'>{workspace}</div>
  <ul style='margin:6px 0 0 0; padding-left:18px;'>{files_list}</ul>
</div>"""
        self._panel.chat.append_token(card)

    def _on_agentic_error(self, msg: str):
        self._panel.chat.append_token(f"\n\n⚠️ Error: {msg}")

    def _on_commands_suggested(self, commands: list, workspace: str):
        """Show approval dialog for each suggested command."""
        from PyQt6.QtWidgets import QMessageBox
        for cmd in commands:
            box = QMessageBox(self)
            box.setWindowTitle("Mash wants to run a command")
            box.setText(f"<b>Allow Mash to run this command?</b><br/><br/><code>{cmd}</code>")
            box.setInformativeText(f"Working directory: {workspace}")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.setIcon(QMessageBox.Icon.Question)
            ret = box.exec()
            if ret == QMessageBox.StandardButton.Yes:
                self._run_command_in_chat(cmd, workspace)

    def _resolve_commands(self, text: str, workspace: str) -> list:
        """Translate natural language intent into real shell commands based on workspace files."""
        t = text.lower()
        files = set(os.listdir(workspace)) if os.path.exists(workspace) else set()
        commands = []

        wants_install = any(w in t for w in ["install", "dependencies", "requirements", "setup"])
        wants_run     = any(w in t for w in ["run", "start", "execute", "launch", "serve"])

        is_python = bool(files & {"requirements.txt", "app.py", "main.py", "manage.py",
                                   "server.py", "Pipfile"})

        if wants_install:
            if "requirements.txt" in files:
                # Use venv to avoid Debian's externally-managed-environment error
                venv_pip = "./venv/bin/pip"
                commands.append("python3 -m venv venv")
                commands.append(f"{venv_pip} install -r requirements.txt")
            elif "package.json" in files:
                commands.append("npm install")
            elif "Pipfile" in files:
                commands.append("pipenv install")
            elif "Cargo.toml" in files:
                commands.append("cargo build")
            elif "pom.xml" in files:
                commands.append("mvn install -q")
            elif "go.mod" in files:
                commands.append("go mod tidy")

        if wants_run:
            # Use venv's python if we just set it up, else system python3
            py = "./venv/bin/python" if (is_python and "requirements.txt" in files) else "python3"
            if "manage.py" in files:
                commands.append(f"{py} manage.py runserver")
            elif "app.py" in files:
                commands.append(f"{py} app.py")
            elif "main.py" in files:
                commands.append(f"{py} main.py")
            elif "server.py" in files:
                commands.append(f"{py} server.py")
            elif "index.js" in files:
                commands.append("node index.js")
            elif "package.json" in files:
                commands.append("npm start")
            elif "Cargo.toml" in files:
                commands.append("cargo run")
            elif "main.go" in files:
                commands.append("go run main.go")
            elif "Makefile" in files:
                commands.append("make")

        # Fallback: if it already looks like a real shell command, pass it through
        if not commands:
            first_word = t.split()[0] if t.split() else ""
            real_bins = {"pip", "pip3", "python", "python3", "node", "npm", "npx",
                         "cargo", "go", "make", "mvn", "java", "gcc", "g++"}
            if first_word in real_bins:
                commands.append(text.strip())

        return commands

    def _run_command_in_chat(self, cmd: str, cwd: str, track_as_run: bool = False):
        """Run command in a background thread, stream output line-by-line to chat."""
        self._panel.chat.setVisible(True)
        self._panel.chat.start_assistant_message()
        self._panel.chat.append_token(f"<code>$ {cmd}</code>\n")
        output_buf = []
        runner = _CommandRunner(cmd, cwd, parent=self)
        runner.output_line.connect(self._panel.chat.append_token)
        runner.output_line.connect(output_buf.append)

        # Track long-running process (servers, etc.) so /stop can kill it
        if track_as_run:
            # Kill any previously tracked run process first
            old = getattr(self, "_run_runner", None)
            if old and old.isRunning():
                old.kill()
            self._run_runner = runner

        def _on_done(code, _cmd=cmd, _cwd=cwd):
            if track_as_run and getattr(self, "_run_runner", None) is runner:
                self._run_runner = None
            full = "".join(output_buf)
            if code != 0 and "address already in use" in full.lower():
                import re as _re
                m = _re.search(r'[Pp]ort (\d+)', full)
                port = m.group(1) if m else "5000"
                self._panel.chat.append_token(
                    f"\n💡 Port {port} is busy. Use <b>/stop</b> then <b>/run</b> again, "
                    f"or say <b>'kill port {port}'</b>."
                )
                self._port_conflict_cmd  = _cmd
                self._port_conflict_cwd  = _cwd
                self._port_conflict_port = port
            elif code == -9 or code == -15:
                pass  # killed by /stop — no need to print exit code
            else:
                self._panel.chat.append_token(
                    "\n✅ Done." if code == 0 else f"\n⚠️ Exit code {code}"
                )
            self._panel.chat.finalize_assistant_message()

        runner.done.connect(_on_done)
        runner.start()
        if not hasattr(self, "_cmd_runners"):
            self._cmd_runners = []
        self._cmd_runners.append(runner)
        runner.finished.connect(lambda: self._cmd_runners.remove(runner) if runner in self._cmd_runners else None)

    def _on_agentic_finished(self):
        self._panel.chat.finalize_assistant_message()
        self._panel.input.set_generating(False)
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._char.set_writing(False)
        self._agentic_worker = None
        self._agentic_vscode_opened = False

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
            self._panel.input.set_generating(False)
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
            self._panel.input.set_generating(False)
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
        self._panel.input.set_generating(False)
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
