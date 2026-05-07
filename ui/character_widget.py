"""CharacterWidget — animated character at a desk, drawn with QPainter."""
import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QLinearGradient,
    QRadialGradient, QPen, QBrush, QFont
)


class CharacterWidget(QWidget):
    """Draws a cute robot character sitting at a glowing desk."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setMaximumHeight(180)
        self._tick = 0.0
        self._blink = 0
        self._blink_counter = 0
        self._thinking = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)  # ~25fps

    def set_thinking(self, thinking: bool):
        self._thinking = thinking
        self.update()

    def _animate(self):
        self._tick += 0.06
        self._blink_counter += 1
        if self._blink_counter > 60:
            self._blink = 1
        if self._blink_counter > 63:
            self._blink = 0
            self._blink_counter = 0
        self.update()

    # ── painting ──────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        self._draw_desk(p, w, h)
        self._draw_lamp(p, w, h)
        self._draw_plant(p, w, h)
        self._draw_laptop(p, w, h)
        self._draw_character(p, w, h)
        if self._thinking:
            self._draw_thinking_dots(p, w, h)

    def _draw_background(self, p, w, h):
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(14, 10, 28))
        grad.setColorAt(1, QColor(8, 6, 18))
        p.fillRect(0, 0, w, h, grad)

        # subtle purple ambient glow centre
        rg = QRadialGradient(w * 0.45, h * 0.55, h * 0.55)
        rg.setColorAt(0, QColor(120, 40, 220, 35))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, h, rg)

    def _draw_desk(self, p, w, h):
        desk_y = h * 0.70
        desk_h = h * 0.14
        # desk surface
        grad = QLinearGradient(0, desk_y, 0, desk_y + desk_h)
        grad.setColorAt(0, QColor(60, 35, 12))
        grad.setColorAt(1, QColor(38, 22, 7))
        path = QPainterPath()
        path.addRoundedRect(QRectF(w * 0.04, desk_y, w * 0.92, desk_h), 6, 6)
        p.fillPath(path, grad)
        # desk highlight strip
        p.setPen(QPen(QColor(100, 65, 25, 120), 1))
        p.drawLine(int(w * 0.06), int(desk_y + 2), int(w * 0.94), int(desk_y + 2))
        # legs
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 22, 7))
        leg_w, leg_h = 10, int(h * 0.10)
        leg_y = int(desk_y + desk_h)
        p.drawRect(int(w * 0.12), leg_y, leg_w, leg_h)
        p.drawRect(int(w * 0.86) - leg_w, leg_y, leg_w, leg_h)

    def _draw_lamp(self, p, w, h):
        base_x = int(w * 0.78)
        desk_y = int(h * 0.70)
        # pole
        p.setPen(QPen(QColor(180, 180, 200), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(base_x, desk_y, base_x - 14, int(desk_y - h * 0.26))
        # arm
        arm_x = base_x - 14
        arm_y = int(desk_y - h * 0.26)
        p.drawLine(arm_x, arm_y, arm_x + 26, arm_y - 12)
        # lamp head
        head_x, head_y = arm_x + 26, arm_y - 12
        shade_path = QPainterPath()
        shade_path.moveTo(head_x - 16, head_y)
        shade_path.lineTo(head_x + 16, head_y)
        shade_path.lineTo(head_x + 10, head_y + 18)
        shade_path.lineTo(head_x - 10, head_y + 18)
        shade_path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(220, 200, 100))
        p.drawPath(shade_path)
        # lamp glow
        glow = QRadialGradient(head_x, head_y + 22, 40)
        glow.setColorAt(0, QColor(255, 240, 150, 55))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRect(head_x - 45, head_y, 90, 80), glow)

    def _draw_plant(self, p, w, h):
        pot_x, pot_y = int(w * 0.88), int(h * 0.62)
        # pot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(130, 60, 20))
        pot = QPainterPath()
        pot.addRoundedRect(QRectF(pot_x - 9, pot_y, 18, 14), 3, 3)
        p.drawPath(pot)
        # cactus body
        p.setBrush(QColor(40, 160, 70))
        p.drawEllipse(QRectF(pot_x - 7, pot_y - 20, 14, 22))
        # arms
        p.drawEllipse(QRectF(pot_x - 14, pot_y - 14, 8, 12))
        p.drawEllipse(QRectF(pot_x + 6, pot_y - 16, 8, 12))

    def _draw_laptop(self, p, w, h):
        desk_y = h * 0.70
        lx, ly = int(w * 0.32), int(desk_y - 34)
        lw, lh = 100, 64
        # base
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(28, 28, 48))
        base = QPainterPath()
        base.addRoundedRect(QRectF(lx, ly + lh - 8, lw, 8), 2, 2)
        p.drawPath(base)
        # screen back
        screen_back = QPainterPath()
        screen_back.addRoundedRect(QRectF(lx + 4, ly, lw - 8, lh - 8), 6, 6)
        p.setBrush(QColor(22, 22, 38))
        p.drawPath(screen_back)
        # screen glow
        glow_grad = QLinearGradient(lx + 4, ly, lx + lw - 8, ly + lh - 8)
        glow_grad.setColorAt(0, QColor(255, 255, 255, 60))
        glow_grad.setColorAt(0.5, QColor(200, 200, 200, 40))
        glow_grad.setColorAt(1, QColor(100, 100, 100, 20))
        screen_rect = QPainterPath()
        screen_rect.addRoundedRect(QRectF(lx + 7, ly + 3, lw - 14, lh - 14), 4, 4)
        p.fillPath(screen_rect, glow_grad)
        # code lines on screen
        p.setPen(QPen(QColor(255, 255, 255, 100), 1.5))
        for i, line_w in enumerate([40, 28, 36, 22, 32]):
            yy = ly + 10 + i * 9
            p.drawLine(lx + 12, yy, lx + 12 + line_w, yy)

    def _draw_character(self, p, w, h):
        desk_y = h * 0.70
        # bob offset
        bob = math.sin(self._tick) * 3.0
        cx, cy = int(w * 0.48), int(desk_y - 72 + bob)

        # body
        p.setPen(Qt.PenStyle.NoPen)
        body_grad = QLinearGradient(cx - 18, cy + 20, cx + 18, cy + 58)
        body_grad.setColorAt(0, QColor(200, 200, 220))
        body_grad.setColorAt(1, QColor(140, 140, 165))
        body_path = QPainterPath()
        body_path.addRoundedRect(QRectF(cx - 18, cy + 20, 36, 38), 10, 10)
        p.fillPath(body_path, body_grad)

        # arms
        p.setBrush(QColor(180, 180, 200))
        arm_angle = math.sin(self._tick * 0.7) * 4
        # left arm
        la_path = QPainterPath()
        la_path.addRoundedRect(QRectF(cx - 30, cy + 24 + arm_angle, 14, 28), 7, 7)
        p.drawPath(la_path)
        # right arm
        ra_path = QPainterPath()
        ra_path.addRoundedRect(QRectF(cx + 16, cy + 24 - arm_angle, 14, 28), 7, 7)
        p.drawPath(ra_path)

        # head
        head_grad = QRadialGradient(cx, cy + 8, 26)
        head_grad.setColorAt(0, QColor(230, 230, 245))
        head_grad.setColorAt(1, QColor(190, 188, 215))
        head_path = QPainterPath()
        head_path.addEllipse(QRectF(cx - 22, cy - 16, 44, 44))
        p.fillPath(head_path, head_grad)

        # ear bumps
        p.setBrush(QColor(200, 198, 220))
        p.drawEllipse(QRectF(cx - 25, cy + 2, 6, 6))
        p.drawEllipse(QRectF(cx + 19, cy + 2, 6, 6))

        # antenna
        p.setPen(QPen(QColor(160, 160, 190), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, int(cy - 16), cx, int(cy - 28))
        glow_r = QRadialGradient(cx, cy - 30, 5)
        glow_r.setColorAt(0, QColor(255, 255, 255, 220))
        glow_r.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRect(cx - 6, int(cy - 36), 12, 12), glow_r)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(cx - 4, cy - 34, 8, 8))

        # eyes
        p.setPen(Qt.PenStyle.NoPen)
        if self._blink == 0:
            # open eyes
            p.setBrush(QColor(20, 15, 40))
            p.drawEllipse(QRectF(cx - 12, cy + 2, 10, 10))
            p.drawEllipse(QRectF(cx + 2, cy + 2, 10, 10))
            # eye shine
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawEllipse(QRectF(cx - 10, cy + 3, 4, 4))
            p.drawEllipse(QRectF(cx + 4, cy + 3, 4, 4))
        else:
            # closed eyes (blink)
            p.setPen(QPen(QColor(20, 15, 40), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(cx - 12, cy + 7, cx - 2, cy + 7)
            p.drawLine(cx + 2, cy + 7, cx + 12, cy + 7)
            p.setPen(Qt.PenStyle.NoPen)

        # mouth — small smile
        mouth_path = QPainterPath()
        mouth_path.moveTo(cx - 6, cy + 16)
        mouth_path.quadTo(cx, cy + 20, cx + 6, cy + 16)
        p.setPen(QPen(QColor(80, 70, 110), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(mouth_path)

    def _draw_thinking_dots(self, p, w, h):
        """Pulsing dots to indicate thinking."""
        desk_y = h * 0.70
        bob = math.sin(self._tick) * 3.0
        cx, cy = int(w * 0.48), int(desk_y - 72 + bob)
        # speech bubble area above head
        for i, offset in enumerate([-12, 0, 12]):
            phase = self._tick * 3 + i * 1.2
            alpha = int(160 + 90 * math.sin(phase))
            r = 3 + math.sin(phase) * 1.5
            p.setBrush(QColor(255, 255, 255, alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx + offset - r / 2, cy - 44 - r / 2, r, r))
