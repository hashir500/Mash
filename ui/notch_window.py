"""NotchWindow — Dynamic Island-style frameless always-on-top Mash window.

States
------
COLLAPSED  : 200×36 pill at top-centre
EXPANDED   : 420×216 card drops down (animation only)
"""
import os
import re
import subprocess
import json
import psutil
from datetime import datetime
from enum import Enum, auto

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication,
    QGraphicsOpacityEffect, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QRect, QRectF, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtSignal, QEvent, QPoint, QPointF, QThread
)
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QLinearGradient,
    QRadialGradient, QFont, QFontDatabase, QCursor, QPixmap
)

from ui.character_widget import CharacterWidget
from ui.chat_widget import ChatWidget
from ui.input_bar import InputBar, SlashMenu
from ui.settings_window import SettingsWindow
from ai.openrouter import StreamWorker
from ai.agentic_worker import AgenticCodingWorker
from PyQt6.QtSvg import QSvgRenderer

from ai.nanobot_wrapper import NanobotWorker
import utils.projects as project_registry

from PyQt6.QtWidgets import QHBoxLayout, QPushButton
import logging

logger = logging.getLogger("mash")


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

        self._notch_locked = True
        self._notch_x = None
        self._notch_y = None

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
        self._panel.input.mode_dropdown.mode_changed.connect(self._char.set_mode)
        self._panel.input.mode_dropdown.mode_changed.connect(self._on_mode_changed)
        self._panel.input.mode_dropdown.mode_changed.connect(
            lambda m: self._panel.input.set_coding_mode(m == "coding")
        )

        self._settings = SettingsWindow(self)
        self._settings.branding_changed.connect(self._update_branding)
        self._settings.animation_changed.connect(self._update_animation)
        self._settings.spotify_toggled.connect(self._update_spotify_enabled)
        self._settings.lock_notch_toggled.connect(self._update_notch_lock)
        
        # Load UI Config
        self._branding_mode = "MASH"
        self._branding_custom = "MASH"
        self._branding_text = "MASH"
        self._anim_mode = "None"
        
        # Animation state
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start(16) # ~60 FPS
        self._gd_cube_y = 0.0
        self._gd_cube_vy = 0.0
        self._gd_spike_x = 220.0
        self._dino_y = 0.0
        self._dino_vy = 0.0
        self._cactus_x = 220.0
        self._car_bounce = 0.0
        self._car_bounce_dir = 1
        self._building_x = 0.0
        self._spotify_song = ""
        self._spotify_artist = ""
        self._spotify_tick = 0
        self._spotify_enabled = False
        self._spotify_scroll_x = 0.0
        self._spotify_scroll_x = 0.0
        
        # Load assets
        assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
        self._spotify_svg = QSvgRenderer(os.path.join(assets_dir, "spotify.svg"))

        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "..", "ai", "ui_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    uicfg = json.load(f)
                    self._branding_mode = uicfg.get("branding_mode", "MASH")
                    self._branding_custom = uicfg.get("branding_text", "MASH")
                    self._anim_mode = uicfg.get("anim_mode", "None")
                    self._spotify_enabled = uicfg.get("spotify_enabled", False)
                    self._notch_locked = uicfg.get("notch_locked", True)
                    self._notch_x = uicfg.get("notch_x")
                    self._notch_y = uicfg.get("notch_y")
                    
                    self._settings.btn_branding_mode.setText(self._branding_mode)
                    self._settings.btn_branding_anim.setText(self._anim_mode)
                    self._settings.check_spotify.setChecked(self._spotify_enabled)
                    self._settings.check_lock_notch.setChecked(self._notch_locked)
                    self._settings.edit_branding.setText(self._branding_custom)
                    self._settings.custom_branding_container.setVisible(self._branding_mode == "Custom")
                    self._refresh_branding()
                    if self._spotify_enabled:
                        self._update_spotify_info()
        except Exception as e:
            logger.error(f"Failed to load UI config: {e}")
        
        self._position_collapsed()

        self._vitals_timer = QTimer(self)
        self._vitals_timer.timeout.connect(self._refresh_branding)
        self._vitals_timer.start(1000)
        
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
        layout.setContentsMargins(8, 8, 8, 8)
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
        if self._notch_x is not None and self._notch_y is not None:
            return QRect(self._notch_x, self._notch_y, PILL_W, PILL_H)
        sr = self._screen_rect()
        cx = sr.x() + sr.width() // 2
        return QRect(cx - PILL_W // 2, sr.y(), PILL_W, PILL_H)

    def _expanded_rect(self):
        c_rect = self._collapsed_rect()
        cx = c_rect.x() + c_rect.width() // 2
        return QRect(cx - CARD_W // 2, c_rect.y(), CARD_W, CARD_H)

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

    def _on_mode_changed(self, mode: str):
        # Clear the injected system prompt when leaving coding mode
        self._coding_system_injected = False

    def _on_stop(self):
        """Interrupt AI generation AND kill any running project process."""
        run_runner = getattr(self, "_run_runner", None)
        if run_runner and run_runner.isRunning():
            run_runner.kill()
            self._panel.chat.setVisible(True)
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⏹ Project process killed.")
            self._panel.chat.finalize_assistant_message()
            self._run_runner = None
        worker = getattr(self, "_agentic_worker", None)
        if worker and worker.isRunning():
            worker.abort()
        sw = getattr(self, "_worker", None)
        if sw and sw.isRunning():
            sw.abort()
        nw = getattr(self, "_nanobot_worker", None)
        if nw and nw.isRunning():
            nw.abort()
        for runner in getattr(self, "_cmd_runners", []):
            if runner.isRunning():
                runner.kill()
        self._panel.input.set_generating(False)
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._char.set_writing(False)
        if self._panel.chat._current_bubble:
            self._panel.chat.append_token("\n⏹ Stopped.")
            self._panel.chat.finalize_assistant_message()

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
            _run_keywords = ["install ", "run ", "execute ", "start ", "pip ", "npm ",
                             "python ", "node ", "make ", "cargo ", "go run"]
            _switch_keywords = ["continue ", "open ", "use ", "switch to ", "go to ",
                                "work on ", "add to ", "update "]
            _last_workspace = getattr(self, "_last_agentic_workspace", None)
            text_lower = text.lower()

            if "kill port" in text_lower or "free port" in text_lower:
                m = re.search(r'(\d{2,5})', text_lower)
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

            if any(kw in text_lower for kw in _switch_keywords):
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

            if _last_workspace and any(kw in text_lower for kw in _run_keywords):
                self._panel.chat.finalize_assistant_message()
                self._panel.input.set_enabled(True)
                self._char.set_thinking(False)
                from PyQt6.QtWidgets import QMessageBox
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

            self._panel.chat.start_assistant_message()
            workspace = getattr(self, "_last_agentic_workspace", None)
            if workspace and os.path.exists(workspace):
                self._panel.chat.append_token(f"🤖 **Nanobot** is updating project in `{os.path.basename(workspace)}`...\n\n")
            else:
                workspace = os.path.expanduser("~/MashProjects/default_project")
                os.makedirs(workspace, exist_ok=True)
                self._last_agentic_workspace = workspace
                self._panel.chat.append_token(f"🤖 **Nanobot** is initializing project in `{os.path.basename(workspace)}`...\n\n")

            self._nanobot_worker = NanobotWorker(
                message=text,
                workspace=workspace,
                api_key=self._api_key,
                model_id=model_id,
                parent=self,
            )
            self._nanobot_worker.output_received.connect(self._panel.chat.append_token)
            self._nanobot_worker.finished.connect(self._on_nanobot_finished)
            self._nanobot_worker.start()
        else:
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

    def _resolve_commands(self, text: str, workspace: str) -> list:
        t = text.lower()
        files = set(os.listdir(workspace)) if os.path.exists(workspace) else set()
        commands = []
        wants_install = any(w in t for w in ["install", "dependencies", "requirements", "setup"])
        wants_run     = any(w in t for w in ["run", "start", "execute", "launch", "serve"])
        is_python = bool(files & {"requirements.txt", "app.py", "main.py", "manage.py", "server.py", "Pipfile"})
        if wants_install:
            if "requirements.txt" in files:
                venv_pip = "./venv/bin/pip"
                commands.append("python3 -m venv venv")
                commands.append(f"{venv_pip} install -r requirements.txt")
            elif "package.json" in files:
                commands.append("npm install")
        if wants_run:
            py = "./venv/bin/python" if (is_python and "requirements.txt" in files) else "python3"
            if "manage.py" in files:
                commands.append(f"{py} manage.py runserver")
            elif "app.py" in files:
                commands.append(f"{py} app.py")
            elif "main.py" in files:
                commands.append(f"{py} main.py")
            elif "package.json" in files:
                commands.append("npm start")
        if not commands:
            first_word = t.split()[0] if t.split() else ""
            real_bins = {"pip", "pip3", "python", "python3", "node", "npm", "npx", "cargo", "go", "make"}
            if first_word in real_bins:
                commands.append(text.strip())
        return commands

    def _run_command_in_chat(self, cmd: str, cwd: str, track_as_run: bool = False):
        self._panel.chat.setVisible(True)
        self._panel.chat.start_assistant_message()
        self._panel.chat.append_token(f"<code>$ {cmd}</code>\n")
        output_buf = []
        runner = _CommandRunner(cmd, cwd, parent=self)
        runner.output_line.connect(self._panel.chat.append_token)
        runner.output_line.connect(output_buf.append)
        if track_as_run:
            old = getattr(self, "_run_runner", None)
            if old and old.isRunning():
                old.kill()
            self._run_runner = runner
        def _on_done(code, _cmd=cmd, _cwd=cwd):
            if track_as_run and getattr(self, "_run_runner", None) is runner:
                self._run_runner = None
            full = "".join(output_buf)
            if code != 0 and "address already in use" in full.lower():
                m = re.search(r'[Pp]ort (\d+)', full)
                port = m.group(1) if m else "5000"
                self._panel.chat.append_token(f"\n💡 Port {port} is busy. Use <b>/stop</b> then <b>/run</b> again, or say <b>'kill port {port}'</b>.")
                self._port_conflict_cmd  = _cmd
                self._port_conflict_cwd  = _cwd
                self._port_conflict_port = port
            elif code == -9 or code == -15:
                pass
            else:
                self._panel.chat.append_token("\n✅ Done." if code == 0 else f"\n⚠️ Exit code {code}")
            self._panel.chat.finalize_assistant_message()
        runner.done.connect(_on_done)
        runner.start()
        if not hasattr(self, "_cmd_runners"):
            self._cmd_runners = []
        self._cmd_runners.append(runner)
        runner.finished.connect(lambda: self._cmd_runners.remove(runner) if runner in self._cmd_runners else None)

    def _on_nanobot_finished(self, success: bool, last_output: str):
        self._panel.chat.finalize_assistant_message()
        self._panel.input.set_generating(False)
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._char.set_writing(False)
        if not success:
            logger.error(f"Nanobot failed: {last_output}")

    def _on_token(self, token: str):
        self._panel.chat.append_token(token)
        if self._char._is_thinking:
            self._char.set_thinking(False)
            self._char.set_writing(True)

    def _on_reasoning(self, text: str):
        self._panel.chat.append_reasoning(text)

    def _on_done(self):
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
        w, h = self.width(), self.height()
        if self._state == State.COLLAPSED or self._animating:
            frac = (h - PILL_H) / max(1, CARD_H - PILL_H)
            frac = max(0.0, min(1.0, frac))
            corner = CORNER_PILL + (CORNER_CARD - CORNER_PILL) * frac
        else:
            corner = CORNER_CARD
        rect = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, corner, corner)
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(24, 24, 27, 230))
        bg.setColorAt(1, QColor(9, 9, 11, 245))
        p.fillPath(path, bg)
        inner_glow = QPainterPath()
        inner_glow.addRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), corner, corner)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawPath(inner_glow)
        border_alpha = int(30 + 20 * self._pulse)
        p.setPen(QPen(QColor(255, 255, 255, border_alpha), 1.0))
        p.drawPath(path)
        if self._state == State.COLLAPSED:
            self._paint_pill_content(p, w, h)

    def _paint_pill_content(self, p, w, h):
        if self._spotify_enabled and self._spotify_song:
            self._draw_spotify_live(p, w, h)
            return
        if self._anim_mode == "None":
            font = QFont("Inter", 10, QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
            p.setFont(font)
            text_rect = QRect(0, 1, w - 28, h)
            p.setPen(QColor(255, 255, 255, int(200 + 55 * self._pulse)))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._branding_text)
        if self._anim_mode == "Geometry Dash":
            self._draw_geometry_dash(p, w, h)
        elif self._anim_mode == "Chrome Dino":
            self._draw_chrome_dino(p, w, h)
        elif self._anim_mode == "Car Drive":
            self._draw_car_drive(p, w, h)
        dot_x = w - 22
        dot_y = h // 2
        dot_r = 5 + self._pulse * 2.5
        rg = QRadialGradient(dot_x, dot_y, dot_r * 2.2)
        rg.setColorAt(0, QColor(255, 255, 255, int(40 * self._pulse)))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(int(dot_x - dot_r * 2.5), int(dot_y - dot_r * 2.5), int(dot_r * 5), int(dot_r * 5), rg)
        p.setBrush(QColor(255, 255, 255, int(200 + 55 * self._pulse)))
        p.drawEllipse(QRectF(dot_x - dot_r / 2, dot_y - dot_r / 2, dot_r, dot_r))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_start_global = event.globalPosition().toPoint()
            if self._state == State.COLLAPSED:
                self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                self.show()
                if getattr(self, "_notch_locked", True):
                    self.expand()
            elif self._state == State.EXPANDED:
                if not self._panel.isVisible() or self._panel._fx.opacity() == 0.0:
                    self._panel.show_animated()
                    self._panel.input.focus()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._state == State.EXPANDED:
                self.collapse()
            else:
                self.close()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._state == State.COLLAPSED:
            if not getattr(self, "_notch_locked", True):
                new_pos = event.globalPosition().toPoint() - self._drag_pos
                self.move(new_pos)
                self._notch_x = new_pos.x()
                self._notch_y = new_pos.y()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._state == State.COLLAPSED:
            if not getattr(self, "_notch_locked", True):
                dist = (event.globalPosition().toPoint() - self._drag_start_global).manhattanLength()
                if dist < 5:
                    self.expand()
                else:
                    self._save_ui_config()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._state == State.EXPANDED:
            if self._settings.isVisible():
                self._settings.hide_animated()
            else:
                self.collapse()
        elif (event.key() == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._state == State.EXPANDED):
            if self._settings.isVisible():
                self._settings.hide_animated()
            else:
                if self._panel.isVisible(): self._panel.hide()
                self.collapse()
                QTimer.singleShot(350, lambda: self._settings.show_animated(self.geometry().center()))
        super().keyPressEvent(event)

    def _on_anim_tick(self):
        if self._spotify_enabled:
            self._spotify_tick += 1
            if self._spotify_tick >= 20:
                self._spotify_tick = 0
                self._update_spotify_info()
            if self._spotify_song != "":
                self._spotify_scroll_x += 0.8
            self.update()
        if self._state != State.COLLAPSED or self._animating:
            return
        if self._anim_mode == "Geometry Dash":
            self._gd_cube_vy += 0.45
            self._gd_cube_y += self._gd_cube_vy
            if self._gd_cube_y > 0:
                self._gd_cube_y = 0
                self._gd_cube_vy = 0
            self._gd_spike_x -= 2.2
            if self._gd_spike_x < -20: self._gd_spike_x = 220
            if 100 < self._gd_spike_x < 115 and self._gd_cube_y == 0:
                self._gd_cube_vy = -6.2
            self.update()
        elif self._anim_mode == "Chrome Dino":
            self._dino_vy += 0.55
            self._dino_y += self._dino_vy
            if self._dino_y > 0:
                self._dino_y = 0
                self._dino_vy = 0
            self._cactus_x -= 2.6
            if self._cactus_x < -20: self._cactus_x = 220
            if 90 < self._cactus_x < 110 and self._dino_y == 0:
                self._dino_vy = -7.2
            self.update()
        elif self._anim_mode == "Car Drive":
            self._building_x -= 1.8
            if self._building_x <= -200:
                self._building_x = 0
            
            self._car_bounce += 0.25 * self._car_bounce_dir
            if self._car_bounce > 2.0:
                self._car_bounce = 2.0
                self._car_bounce_dir = -1
            elif self._car_bounce < 0.0:
                self._car_bounce = 0.0
                self._car_bounce_dir = 1
            self.update()

    def _draw_car_drive(self, p, w, h):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Apply clipping to keep animation inside the pill bounds
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0, 0, w, h), CORNER_PILL, CORNER_PILL)
        p.setClipPath(clip_path)
        
        # Draw parallax buildings
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(60, 60, 65, 150))
        
        for offset in [0, 200]:
            bx = self._building_x + offset
            if bx + 200 < 0 or bx > w:
                continue
            p.drawRect(QRectF(bx + 10, h - 25, 30, 25))
            p.drawRect(QRectF(bx + 45, h - 35, 40, 35))
            p.drawRect(QRectF(bx + 90, h - 20, 25, 20))
            p.drawRect(QRectF(bx + 120, h - 30, 35, 30))
            p.drawRect(QRectF(bx + 160, h - 15, 30, 15))

            p.setBrush(QColor(255, 255, 200, 180))
            for wx in [bx + 50, bx + 65]:
                for wy in [h - 30, h - 20, h - 10]:
                    p.drawRect(QRectF(wx, wy, 5, 5))
            p.setBrush(QColor(60, 60, 65, 150))
        
        # Draw road line
        p.setPen(QPen(QColor(100, 100, 100, 200), 1))
        p.drawLine(0, h - 2, w, h - 2)

        # Draw the Car
        car_y = h - 12 + self._car_bounce
        car_x = (w // 2) - 25
        
        # Tires
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20))
        p.drawEllipse(QRectF(car_x + 8, car_y + 4, 8, 8))
        p.drawEllipse(QRectF(car_x + 36, car_y + 4, 8, 8))
        
        # Car body (modern sleek profile)
        p.setBrush(QColor(40, 120, 255))
        body_path = QPainterPath()
        body_path.moveTo(car_x + 5, car_y + 4)
        body_path.lineTo(car_x + 2, car_y - 2)
        body_path.lineTo(car_x + 8, car_y - 6)
        body_path.lineTo(car_x + 18, car_y - 12)
        body_path.lineTo(car_x + 35, car_y - 12)
        body_path.lineTo(car_x + 45, car_y - 4)
        body_path.lineTo(car_x + 48, car_y + 4)
        body_path.closeSubpath()
        p.drawPath(body_path)
        
        # Windows
        p.setBrush(QColor(20, 20, 30, 220))
        win_path = QPainterPath()
        win_path.moveTo(car_x + 10, car_y - 5)
        win_path.lineTo(car_x + 19, car_y - 10)
        win_path.lineTo(car_x + 28, car_y - 10)
        win_path.lineTo(car_x + 28, car_y - 5)
        win_path.closeSubpath()
        p.drawPath(win_path)
        
        win_path2 = QPainterPath()
        win_path2.moveTo(car_x + 30, car_y - 5)
        win_path2.lineTo(car_x + 30, car_y - 10)
        win_path2.lineTo(car_x + 36, car_y - 10)
        win_path2.lineTo(car_x + 42, car_y - 5)
        win_path2.closeSubpath()
        p.drawPath(win_path2)

        # Headlight beam
        rg = QRadialGradient(car_x + 5, car_y, 25)
        rg.setColorAt(0, QColor(255, 255, 200, 150))
        rg.setColorAt(1, QColor(255, 255, 200, 0))
        p.setBrush(rg)
        p.drawEllipse(QRectF(car_x - 20, car_y - 12, 50, 25))

        # Taillight
        p.setBrush(QColor(255, 50, 50))
        p.drawRect(QRectF(car_x + 46, car_y - 2, 2, 4))
        
        p.restore()

    def _update_spotify_info(self):
        try:
            # Use systemd-run as a oneshot service to reliably escape AppArmor/Snap confinement
            # (e.g., if mash is started from desktop-icons-ng, it inherits a restrictive profile)
            result = subprocess.run(
                ["systemd-run", "--user", "-P", "--quiet", "-p", "Type=oneshot", "/bin/bash", "/home/hashir/Documents/mash/get_spotify.sh"],
                capture_output=True, text=True, timeout=2.0
            )
            output = result.stdout
            
            if result.returncode != 0 or not output:
                logger.error(f"Spotify command failed. RC={result.returncode}, STDOUT='{result.stdout}', STDERR='{result.stderr}'")
            
            t_m = re.search(r"'xesam:title': <'(.*?)'>", output)
            a_m = re.search(r"'xesam:artist': <\['(.*?)'\]>", output)
            
            title = t_m.group(1).strip() if t_m else ""
            artist = a_m.group(1).strip() if a_m else ""

            if title:
                if title != self._spotify_song:
                    self._spotify_song = title
                    self._spotify_scroll_x = 0.0
                self._spotify_artist = artist
            else:
                self._spotify_song, self._spotify_artist = "", ""
                
        except Exception as e:
            logger.error(f"Spotify update failed: {e}")
            self._spotify_song, self._spotify_artist = "", ""

    def _draw_spotify_live(self, p, w, h):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon_size = 18
        icon_x, icon_y = 35 - icon_size / 2, h / 2 - icon_size / 2
        if self._spotify_svg.isValid():
            self._spotify_svg.render(p, QRectF(icon_x, icon_y, icon_size, icon_size))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(30, 215, 96))
            p.drawEllipse(QPointF(35, h // 2), 8.5, 8.5)
        p.setFont(QFont("Inter", 9, QFont.Weight.Medium))
        p.setPen(QColor(255, 255, 255, 230))
        display_text = self._spotify_song if self._spotify_song else "Spotify"
        if self._spotify_song and self._spotify_artist:
            display_text += f" • {self._spotify_artist}"
        metrics = p.fontMetrics()
        text_w = metrics.horizontalAdvance(display_text)
        available_w = w - 85
        gap = 60
        off_x = (self._spotify_scroll_x % (text_w + gap)) if text_w > available_w else 0
        p.setClipRect(QRect(55, 0, available_w, h))
        draw_x = 55 - off_x
        p.drawText(QRect(int(draw_x), 0, text_w + 10, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_text)
        if text_w > available_w:
            p.drawText(QRect(int(draw_x + text_w + gap), 0, text_w + 10, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_text)
        p.setClipping(False)
        p.restore()

    def _draw_chrome_dino(self, p, w, h):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        floor_y = (h // 2) + 6
        d_x, d_y = (w // 2) - 40, floor_y + self._dino_y
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(160, 160, 160, 240))
        p.drawRect(QRectF(d_x + 10, d_y - 18, 10, 6))
        p.drawRect(QRectF(d_x + 15, d_y - 12, 5, 2))
        p.setBrush(QColor(5, 5, 5, 180))
        p.drawRect(QRectF(d_x + 12, d_y - 16, 2, 2))
        p.setBrush(QColor(160, 160, 160, 240))
        p.drawRect(QRectF(d_x + 8, d_y - 12, 4, 4))
        p.drawRect(QRectF(d_x + 2, d_y - 10, 8, 7))
        p.drawRect(QRectF(d_x, d_y - 8, 2, 3))
        p.drawRect(QRectF(d_x - 2, d_y - 6, 2, 2))
        p.drawRect(QRectF(d_x + 10, d_y - 7, 3, 2))
        p.drawRect(QRectF(d_x + 3, d_y - 3, 3, 3))
        p.drawRect(QRectF(d_x + 7, d_y - 3, 3, 3))
        if 10 < self._cactus_x < w - 20:
            p.setPen(QPen(QColor(80, 120, 80, 220), 1.5))
            p.setBrush(QColor(80, 120, 80, 40))
            p.drawRoundedRect(QRectF(self._cactus_x + 3, floor_y - 12, 4, 12), 1, 1)
            p.drawRoundedRect(QRectF(self._cactus_x - 2, floor_y - 8, 3, 5), 1, 1)
            p.drawRoundedRect(QRectF(self._cactus_x + 9, floor_y - 10, 3, 6), 1, 1)
        p.restore()

    def _draw_geometry_dash(self, p, w, h):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        floor_y = (h // 2) + 6
        cube_size, cube_x = 11, (w // 2) - 30
        cube_y = floor_y + self._gd_cube_y
        p.setPen(QPen(QColor(0, 255, 255, 180), 1.5))
        p.setBrush(QColor(0, 255, 255, 40))
        p.drawRoundedRect(QRectF(cube_x, cube_y - cube_size, cube_size, cube_size), 2, 2)
        if 10 < self._gd_spike_x < w - 20:
            p.setPen(QPen(QColor(255, 50, 50, 180), 1.5))
            p.setBrush(QColor(255, 50, 50, 40))
            spike_path = QPainterPath()
            spike_path.moveTo(self._gd_spike_x, floor_y)
            spike_path.lineTo(self._gd_spike_x + 5, floor_y - 10)
            spike_path.lineTo(self._gd_spike_x + 10, floor_y)
            spike_path.closeSubpath()
            p.drawPath(spike_path)
        p.restore()

    def _update_animation(self, anim):
        self._anim_mode = anim
        self._save_ui_config()
        self.update()

    def _update_spotify_enabled(self, enabled):
        self._spotify_enabled = enabled
        if enabled:
            # Force an immediate, fresh update when re-enabled
            self._spotify_tick = 20 
            self._spotify_song = ""
            self._spotify_artist = ""
            self._update_spotify_info()
        self._save_ui_config()
        self.update()


    def _update_branding(self, mode, text):
        self._branding_mode, self._branding_custom = mode, text
        self._refresh_branding()
        self._save_ui_config()

    def _update_notch_lock(self, locked):
        self._notch_locked = locked
        self._save_ui_config()

    def _save_ui_config(self):
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "..", "ai", "ui_config.json")
            data = {
                "branding_mode": self._branding_mode, 
                "branding_text": self._branding_custom, 
                "anim_mode": self._anim_mode, 
                "spotify_enabled": self._spotify_enabled, 
                "auto_collapse": True,
                "notch_locked": getattr(self, "_notch_locked", True),
                "notch_x": getattr(self, "_notch_x", None),
                "notch_y": getattr(self, "_notch_y", None)
            }
            with open(cfg_path, "w") as f: json.dump(data, f, indent=2)
        except Exception as e: logger.error(f"Failed to save UI config: {e}")

    def _refresh_branding(self):
        mode = self._branding_mode
        if mode == "MASH": self._branding_text = "MASH"
        elif mode == "Date": self._branding_text = datetime.now().strftime("%b %d").upper()
        elif mode == "Time": self._branding_text = datetime.now().strftime("%I:%M %p").upper()
        elif mode == "Memory Usage":
            mem = psutil.virtual_memory()
            self._branding_text = f"MEM {mem.used / (1024**3):.1f} GB"
        elif mode == "CPU Usage": self._branding_text = f"CPU {int(psutil.cpu_percent())}%"
        elif mode == "Custom": self._branding_text = self._branding_custom.upper()
        self.update()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and not self._panel.isActiveWindow() and self._state == State.EXPANDED:
                QTimer.singleShot(50, self._maybe_collapse)
        super().changeEvent(event)

    def closeEvent(self, event):
        self._raise_timer.stop()
        self._panel.close()
        if hasattr(self, '_settings'): self._settings.close()
        if self._worker: self._worker.abort(); self._worker.wait(2000)
        super().closeEvent(event)
