"""CharacterWidget — Smooth glowing vector robot face."""
import math
import random
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QRadialGradient, QPen, QLinearGradient

# Fixed neural node positions (relative to center, normalized -1..1)
_NODE_POSITIONS = [
    ( 0.00, -0.55),  # top center
    (-0.38, -0.30),  # top left
    ( 0.38, -0.30),  # top right
    (-0.55,  0.05),  # mid left
    ( 0.55,  0.05),  # mid right
    (-0.30,  0.40),  # bot left
    ( 0.30,  0.40),  # bot right
    ( 0.00,  0.60),  # bottom center
]

# Edges connecting nodes (index pairs)
_EDGES = [
    (0, 1), (0, 2),
    (1, 2), (1, 3),
    (2, 4),
    (3, 4), (3, 5),
    (4, 6),
    (5, 6), (5, 7),
    (6, 7),
    (0, 3), (0, 4),
]

class CharacterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tick = 0
        self._is_thinking = False
        self._think_tick = 0

        # Per-node firing phase offsets (random so they don't all pulse together)
        self._node_phases = [random.uniform(0, math.pi * 2) for _ in _NODE_POSITIONS]
        # Per-edge signal travel positions (0..1, -1 = inactive)
        self._edge_signals = [-1.0] * len(_EDGES)
        self._edge_timers = [random.uniform(0, 3.0) for _ in _EDGES]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_anim)
        self._timer.start(30)

        self._anim_state = "LOOKING"
        self._state_tick = 0
        self._blink_factor = 1.0
        self._is_blinking = False

    # ── Public API ─────────────────────────────────────────────────────────

    def set_thinking(self, thinking: bool):
        self._is_thinking = thinking
        self._think_tick = 0
        if thinking:
            # Reset signal positions
            self._edge_signals = [-1.0] * len(_EDGES)
        self.update()

    # ── Animation loop ─────────────────────────────────────────────────────

    def _update_anim(self):
        self._tick += 1
        if self._is_thinking:
            self._think_tick += 1
            dt = 0.030  # seconds per tick
            # Advance signal "bullets" along edges
            for i in range(len(_EDGES)):
                self._edge_timers[i] -= dt
                if self._edge_timers[i] <= 0:
                    self._edge_signals[i] = 0.0
                    self._edge_timers[i] = random.uniform(0.4, 1.8)
                if self._edge_signals[i] >= 0:
                    self._edge_signals[i] += 0.035
                    if self._edge_signals[i] > 1.0:
                        self._edge_signals[i] = -1.0
        else:
            # Idle blink
            if self._is_blinking:
                self._blink_factor -= 0.35
                if self._blink_factor <= 0.0:
                    self._blink_factor = 0.0
                    self._is_blinking = False
            elif self._anim_state != "YAWNING":
                if self._blink_factor < 1.0:
                    self._blink_factor = min(1.0, self._blink_factor + 0.2)

            # Idle state machine
            if self._anim_state == "LOOKING":
                self._state_tick += 1
                if self._state_tick > 150 and random.random() < 0.3:
                    self._anim_state = "ROLL_EYES"
                    self._state_tick = 0
                if random.random() < 0.02 and not self._is_blinking and self._blink_factor == 1.0:
                    self._is_blinking = True
            elif self._anim_state == "ROLL_EYES":
                self._state_tick += 1
                if self._state_tick > 60:
                    self._anim_state = "YAWNING"
                    self._state_tick = 0
            elif self._anim_state == "YAWNING":
                self._state_tick += 1
                if self._state_tick < 20:
                    self._blink_factor = max(0.1, 1.0 - self._state_tick / 20.0)
                elif self._state_tick > 60:
                    self._blink_factor = min(1.0, 0.1 + (self._state_tick - 60) / 20.0)
                else:
                    self._blink_factor = 0.1
                if self._state_tick > 80:
                    self._anim_state = "LOOKING"
                    self._state_tick = 0

        self.update()

    # ── Drawing helpers ────────────────────────────────────────────────────

    def _draw_glowing_pill(self, p, rect, inner=None, glow_alpha=140):
        cx, cy = rect.center().x(), rect.center().y()
        radius = max(rect.width(), rect.height()) * 1.8

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        grad = QRadialGradient(cx, cy, radius)
        grad.setColorAt(0.0, QColor(0, 100, 255, glow_alpha))
        grad.setColorAt(0.4, QColor(0, 50, 255, 40))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setBrush(inner or QColor(230, 245, 255))
        corner_r = min(rect.width(), rect.height()) / 2.0
        p.drawRoundedRect(rect, corner_r, corner_r)

    def _node_xy(self, idx, cx, cy, rx, ry):
        nx, ny = _NODE_POSITIONS[idx]
        return cx + nx * rx, cy + ny * ry

    # ── Thinking / Neural Brain Animation ─────────────────────────────────

    def _draw_thinking_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._think_tick
        # Brain network occupies most of the space
        rx, ry = w * 0.42, h * 0.42

        # ── Rotating outer rings (machine feel) ─────────────────────────
        for ring_idx, (ring_r, speed, dashes, width, alpha) in enumerate([
            (min(w, h) * 0.47, 0.012, 6, 1.2, 35),
            (min(w, h) * 0.38, -0.020, 4, 0.8, 25),
        ]):
            angle_offset = t * speed
            pen_color = QColor(0, 120, 255, alpha)
            dash_arc = 360.0 / (dashes * 2)

            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            pen = QPen(pen_color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            for d in range(dashes):
                start_angle = int((angle_offset * 180 / math.pi + d * 360 / dashes) * 16)
                span_angle = int(dash_arc * 16)
                ring_rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
                p.drawArc(ring_rect, start_angle, span_angle)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # ── Draw edges ───────────────────────────────────────────────────
        for i, (a, b) in enumerate(_EDGES):
            ax, ay = self._node_xy(a, cx, cy, rx, ry)
            bx, by = self._node_xy(b, cx, cy, rx, ry)

            # Static dim edge
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            pen = QPen(QColor(0, 60, 180, 35), 0.8)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))

            # Traveling signal bullet
            sig = self._edge_signals[i]
            if sig >= 0:
                sx = ax + (bx - ax) * sig
                sy = ay + (by - ay) * sig

                # Glow trail behind bullet
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                trail_len = 0.25
                t0 = max(0, sig - trail_len)
                tx0 = ax + (bx - ax) * t0
                ty0 = ay + (by - ay) * t0
                lg = QLinearGradient(tx0, ty0, sx, sy)
                lg.setColorAt(0.0, QColor(0, 0, 0, 0))
                lg.setColorAt(1.0, QColor(0, 180, 255, 200))
                pen2 = QPen(QColor(0, 180, 255, 200), 1.5)
                p.setPen(pen2)
                p.drawLine(QPointF(tx0, ty0), QPointF(sx, sy))

                # Bright bullet head
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                g = QRadialGradient(sx, sy, 6)
                g.setColorAt(0.0, QColor(200, 230, 255, 255))
                g.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QRectF(sx - 6, sy - 6, 12, 12))

        # ── Draw nodes ───────────────────────────────────────────────────
        for i, (nx, ny) in enumerate(_NODE_POSITIONS):
            x = cx + nx * rx
            y = cy + ny * ry
            phase = self._node_phases[i]
            pulse = 0.5 + 0.5 * math.sin(t * 0.1 + phase)
            nr = 4 + 3 * pulse
            alpha = int(120 + 135 * pulse)

            # Glow
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            gg = QRadialGradient(x, y, nr * 3.5)
            gg.setColorAt(0.0, QColor(0, 160, 255, alpha))
            gg.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(gg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x - nr * 3.5, y - nr * 3.5, nr * 7, nr * 7))

            # Core
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setBrush(QColor(200, 230, 255, min(255, alpha)))
            p.drawEllipse(QRectF(x - nr / 2, y - nr / 2, nr, nr))

        # ── Scan line sweeping top to bottom ────────────────────────────
        scan_y = cy - ry + ((t * 1.8) % (ry * 2))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        scan_grad = QLinearGradient(cx - rx, scan_y, cx + rx, scan_y)
        scan_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        scan_grad.setColorAt(0.5, QColor(0, 160, 255, 30))
        scan_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(cx - rx, scan_y - 1, rx * 2, 2), scan_grad)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    # ── paintEvent ────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self._is_thinking:
            self._draw_thinking_anim(p, w, h)
            return

        # ── Idle face ─────────────────────────────────────────────────────
        float_y = math.sin(self._tick * 0.05) * 6.0
        look_x, look_y = 0.0, 0.0
        mouth_w, mouth_h = 32, 16

        if self._anim_state == "ROLL_EYES":
            progress = self._state_tick / 60.0
            if progress < 0.2:
                look_y = -20 * (progress / 0.2)
                look_x = -12 * (progress / 0.2)
            elif progress < 0.6:
                look_y = -20
                look_x = -12 + 24 * ((progress - 0.2) / 0.4)
            elif progress < 0.8:
                look_y, look_x = -20, 12
            else:
                f = (progress - 0.8) / 0.2
                look_y = -20 * (1 - f)
                look_x = 12 * (1 - f)
        elif self._anim_state == "YAWNING":
            yp = 0.0
            if self._state_tick < 20:
                yp = self._state_tick / 20.0
            elif self._state_tick > 60:
                yp = 1.0 - (self._state_tick - 60) / 20.0
            else:
                yp = 1.0
            mouth_w = 32 - yp * 10
            mouth_h = 16 + yp * 44
            float_y -= yp * 8.0
        elif self._anim_state == "LOOKING":
            look_x = math.cos(self._tick * 0.03) * 4.0
            look_y = math.sin(self._tick * 0.02) * 2.0

        eye_w = 48
        eye_h = max(4.0, 76 * self._blink_factor)
        eye_spacing = 84

        left_eye_x  = cx - eye_spacing / 2.0 - eye_w / 2.0 + look_x
        right_eye_x = cx + eye_spacing / 2.0 - eye_w / 2.0 + look_x
        eye_y = cy - eye_h / 2.0 + float_y + look_y - 16

        mouth_x = cx - mouth_w / 2.0 + look_x * 1.2
        mouth_y = cy + 34 + float_y + (mouth_h / 2.0 if self._anim_state == "YAWNING" else 0)
        yawn_off = mouth_h if self._anim_state == "YAWNING" else 0

        self._draw_glowing_pill(p, QRectF(left_eye_x, eye_y, eye_w, eye_h))
        self._draw_glowing_pill(p, QRectF(right_eye_x, eye_y, eye_w, eye_h))
        self._draw_glowing_pill(p, QRectF(mouth_x, mouth_y - yawn_off, mouth_w, mouth_h))
