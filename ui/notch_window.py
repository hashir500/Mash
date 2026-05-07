"""NotchWindow — Dynamic Island-style frameless always-on-top Mash window.

States
------
COLLAPSED  : 200×36 pill at top-centre
EXPANDED   : 420×216 card drops down (animation only)
"""
import os, math
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
        
        self.input = InputBar()
        
        bg_layout.addWidget(self.chat)
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
        y = pr.y() + pr.height() + 10
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
        self._content.setGeometry(0, PILL_H, CARD_W, CARD_H - PILL_H)
        self._content.setVisible(False)
        self._content.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._char = CharacterWidget()
        layout.addWidget(self._char)

    def _setup_animations(self):
        self._geo_anim = QPropertyAnimation(self, b"geometry")
        self._geo_anim.setDuration(ANIM_MS)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
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
        self._content.setGeometry(0, PILL_H, CARD_W, CARD_H - PILL_H)
        self._geo_anim.setStartValue(self.geometry())
        self._geo_anim.setEndValue(self._expanded_rect())
        self._geo_anim.start()

    def collapse(self):
        if self._state == State.COLLAPSED or self._animating:
            return
        self._expanding = False
        self._panel.hide_animated()
        self._animating = True
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
        self.update()

    def _maybe_collapse(self):
        if self._state == State.EXPANDED and not self._animating:
            self.collapse()
            QTimer.singleShot(ANIM_MS + 50, self._ensure_on_top)

    def _on_submit(self, text: str):
        if not self._api_key:
            self._panel.chat.start_assistant_message()
            self._panel.chat.append_token("⚠️  No API key found. Set OPENROUTER_API_KEY in .env")
            self._panel.chat.finalize_assistant_message()
            return

        self._panel.chat.setVisible(True)
        self._panel.chat.add_user_message(text)
        self._history.append({"role": "user", "content": text})
        self._panel.input.set_enabled(False)
        self._char.set_thinking(True)
        self._panel.chat.start_assistant_message()

        self._worker = StreamWorker(list(self._history), self._api_key, self)
        self._worker.token_received.connect(self._on_token)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_token(self, token: str):
        self._panel.chat.append_token(token)
        self._char.set_thinking(False)

    def _on_done(self):
        self._panel.chat.finalize_assistant_message()
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
        self._worker = None

    def _on_error(self, msg: str):
        self._panel.chat.append_token(f"\n\n⚠️  Error: {msg}")
        self._panel.chat.finalize_assistant_message()
        self._panel.input.set_enabled(True)
        self._char.set_thinking(False)
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
        elif frac > 0.6:
            self._paint_pill_label(p, w)

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
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()

    def mouseMoveEvent(self, event):
        if (event.buttons() == Qt.MouseButton.LeftButton
                and self._state == State.COLLAPSED):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def enterEvent(self, event):
        if self._state == State.EXPANDED:
            self._panel.show_animated()
            self._panel.input.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        QTimer.singleShot(400, self._check_leave)
        super().leaveEvent(event)

    def _check_leave(self):
        # Only hide if the mouse is neither in the main notch nor in the floating panel
        if not (self.geometry().contains(QCursor.pos()) or self._panel.geometry().contains(QCursor.pos())):
            self._panel.hide_animated()

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
