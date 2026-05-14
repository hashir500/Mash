"""CharacterWidget — Smooth glowing vector robot face."""
import math
import random
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QRadialGradient, QPen, QLinearGradient, QPainterPath, QPixmap

# Neural network constants
_NODE_POSITIONS = [
    ( 0.00, -0.55), (-0.38, -0.30), ( 0.38, -0.30),
    (-0.55,  0.05), ( 0.55,  0.05), (-0.30,  0.40),
    ( 0.30,  0.40), ( 0.00,  0.60),
]
_EDGES = [
    (0,1),(0,2),(1,2),(1,3),(2,4),(3,4),(3,5),(4,6),(5,6),(5,7),(6,7),(0,3),(0,4),
]


class CharacterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tick = 0
        self._is_thinking = False
        self._think_tick = 0
        self._is_writing = False
        self._write_tick = 0
        self._write_lines = []          # accumulated scribble lines
        self._send_tick = -1            # -1 = not sending yet
        self._plane_x = 0.0
        self._plane_y = 0.0

        self._node_phases = [random.uniform(0, math.pi * 2) for _ in _NODE_POSITIONS]
        self._edge_signals = [-1.0] * len(_EDGES)
        self._edge_timers  = [random.uniform(0, 3.0) for _ in _EDGES]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_anim)
        self._timer.start(30)

        self._anim_state   = "LOOKING"
        self._state_tick   = 0
        self._blink_factor = 1.0
        self._is_blinking  = False
        self._mode         = "general"
        self._idle_anim    = "orb"   # orb | pulse | orbit

    # ── Public API ─────────────────────────────────────────────────────────

    def set_thinking(self, thinking: bool):
        self._is_thinking = thinking
        self._think_tick  = 0
        if thinking:
            self._edge_signals = [-1.0] * len(_EDGES)
            self._is_writing = False
            self._send_tick  = -1
        self.update()

    def set_writing(self, writing: bool):
        """Called when the AI starts/stops streaming its response."""
        self._is_thinking = False
        if writing:
            self._is_writing = True
            self._write_tick = 0
            self._write_lines = []
            self._send_tick  = -1
        else:
            # Start the paper-airplane send sequence
            self._is_writing = False
            self._send_tick  = 0
            self._plane_x    = 0.0
            self._plane_y    = 0.0
        self.update()

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    def set_idle_anim(self, anim: str):
        """Switch idle animation: 'orb', 'pulse', or 'orbit'."""
        self._idle_anim = anim
        self.update()

    # ── Animation loop ─────────────────────────────────────────────────────

    def _update_anim(self):
        self._tick += 1

        if self._is_thinking:
            self._think_tick += 1
            dt = 0.030
            for i in range(len(_EDGES)):
                self._edge_timers[i] -= dt
                if self._edge_timers[i] <= 0:
                    self._edge_signals[i] = 0.0
                    self._edge_timers[i]  = random.uniform(0.4, 1.8)
                if self._edge_signals[i] >= 0:
                    self._edge_signals[i] += 0.035
                    if self._edge_signals[i] > 1.0:
                        self._edge_signals[i] = -1.0

        elif self._is_writing:
            self._write_tick += 1
            # Add a new scribble point every ~8 ticks
            if self._write_tick % 8 == 0:
                self._write_lines.append(self._write_tick)

        elif self._send_tick >= 0:
            self._send_tick += 1
            # Phase 1 (0-30): paper folds into airplane (just visualised)
            # Phase 2 (30-90): airplane launches right and up, fading
            if self._send_tick > 90:
                self._send_tick  = -1   # back to idle
                self._anim_state = "LOOKING"
                self._state_tick = 0

        else:
            # Idle blinking / state machine
            if self._is_blinking:
                self._blink_factor -= 0.35
                if self._blink_factor <= 0.0:
                    self._blink_factor = 0.0
                    self._is_blinking  = False
            elif self._anim_state != "YAWNING":
                self._blink_factor = min(1.0, self._blink_factor + 0.2)

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

    def _draw_glowing_pill(self, p, rect, inner=None, glow_alpha=120):
        cx, cy = rect.center().x(), rect.center().y()
        # More contained, premium glow
        radius = max(rect.width(), rect.height()) * 1.5
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        grad = QRadialGradient(cx, cy, radius)
        # Using Electric Indigo for a more modern glow
        grad.setColorAt(0.0, QColor(99, 102, 241, glow_alpha))
        grad.setColorAt(0.5, QColor(79, 70, 229, 30))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))
        
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setBrush(inner or QColor(248, 250, 252)) # Slate-50
        corner_r = min(rect.width(), rect.height()) / 2.0
        p.drawRoundedRect(rect, corner_r, corner_r)

    def _node_xy(self, idx, cx, cy, rx, ry):
        nx, ny = _NODE_POSITIONS[idx]
        return cx + nx * rx, cy + ny * ry

    # ── Writing animation ──────────────────────────────────────────────────

    def _draw_writing_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._write_tick
        float_y = math.sin(t * 0.07) * 3.0  # subtle hover, leaning into work

        # ── Paper ────────────────────────────────────────────────────────
        paper_w, paper_h = 90, 68
        paper_x = cx - paper_w / 2.0
        paper_y = cy + 8 + float_y

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(QPen(QColor(200, 210, 255, 60), 1))
        p.setBrush(QColor(15, 15, 30, 200))
        p.drawRoundedRect(QRectF(paper_x, paper_y, paper_w, paper_h), 6, 6)

        # Horizontal ruled lines on paper
        for i in range(4):
            ly = paper_y + 14 + i * 14
            p.setPen(QPen(QColor(100, 130, 255, 30), 1))
            p.drawLine(QPointF(paper_x + 8, ly), QPointF(paper_x + paper_w - 8, ly))

        # ── Scribble writing (lines grow progressively) ──────────────────
        line_coords = [
            (paper_x + 8, paper_y + 14, paper_x + paper_w - 8, paper_y + 14),
            (paper_x + 8, paper_y + 28, paper_x + paper_w - 18, paper_y + 28),
            (paper_x + 8, paper_y + 42, paper_x + paper_w - 8, paper_y + 42),
            (paper_x + 8, paper_y + 56, paper_x + paper_w - 24, paper_y + 56),
        ]
        num_lines = len(self._write_lines)
        for i, (x0, y0, x1, y1) in enumerate(line_coords):
            if i < num_lines:
                # Full line written
                p.setPen(QPen(QColor(180, 210, 255, 180), 1.5))
                p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            elif i == num_lines:
                # Currently being written — partial
                progress = (t % 30) / 30.0
                mid_x = x0 + (x1 - x0) * progress
                p.setPen(QPen(QColor(200, 230, 255, 220), 1.5))
                p.drawLine(QPointF(x0, y0), QPointF(mid_x, y0))

                # Pen cursor glow at tip
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                g = QRadialGradient(mid_x, y0, 8)
                g.setColorAt(0, QColor(0, 180, 255, 200))
                g.setColorAt(1, QColor(0, 0, 0, 0))
                p.setBrush(g)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(mid_x - 8, y0 - 8, 16, 16))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # ── Face (eyes looking DOWN at paper) ────────────────────────────
        look_y = 14.0  # gaze aimed down at work
        look_x = math.sin(t * 0.1) * 3.0  # slight side drift (reading)

        eye_w, eye_h = 42, max(4.0, 60 * 0.5)  # half-open, focused squint
        eye_spacing = 80
        eye_y = cy - eye_h / 2.0 + float_y - 38 + look_y
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0 + look_x
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0 + look_x

        self._draw_glowing_pill(p, QRectF(lx, eye_y, eye_w, eye_h), glow_alpha=160)
        self._draw_glowing_pill(p, QRectF(rx, eye_y, eye_w, eye_h), glow_alpha=160)

        # ── Arms / hands ─────────────────────────────────────────────────
        arm_y_top = cy - 30 + float_y
        arm_y_bot = paper_y + 10

        # Left arm (resting on paper)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setPen(QPen(QColor(0, 80, 200, 80), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - 55, arm_y_top), QPointF(cx - 30, arm_y_bot))

        # Right arm (holding pen, actively writing) — slight up-down bob
        pen_bob = math.sin(t * 0.25) * 3
        p.setPen(QPen(QColor(0, 100, 255, 100), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx + 55, arm_y_top), QPointF(cx + 20, arm_y_bot + pen_bob))

        # Pen nib
        pen_tip_x = cx + 20
        pen_tip_y = arm_y_bot + pen_bob
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(QPen(QColor(220, 235, 255, 220), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(pen_tip_x, pen_tip_y), QPointF(pen_tip_x + 8, pen_tip_y + 10))

    # ── Sending / paper airplane animation ────────────────────────────────

    def _draw_sending_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._send_tick

        if t < 30:
            # Phase 1: Fold — paper squishes into a triangle (scale-down + rotate)
            progress = t / 30.0
            paper_w = 90 * (1 - progress * 0.7)
            paper_h = 68 * (1 - progress * 0.85)
            px = cx - paper_w / 2.0
            py = cy + 8

            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.save()
            p.translate(cx, cy + 8 + paper_h / 2)
            p.rotate(progress * 20)
            p.translate(-cx, -(cy + 8 + paper_h / 2))

            p.setPen(QPen(QColor(200, 210, 255, int(200 * (1 - progress * 0.3))), 1))
            p.setBrush(QColor(15, 15, 30, 200))
            p.drawRoundedRect(QRectF(cx - paper_w / 2, cy + 8, paper_w, paper_h), 4, 4)
            p.restore()

        else:
            # Phase 2: Launch airplane off screen to the right and up
            flight = (t - 30) / 60.0  # 0..1
            eased = 1 - (1 - flight) ** 3  # ease in cubic

            plane_x = cx + eased * (w * 0.7)
            plane_y = cy + 8 - eased * (h * 0.6)
            plane_size = 24 * (1 - eased * 0.5)
            alpha = int(255 * (1 - eased))

            # Draw paper airplane shape
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.save()
            angle = -35 - eased * 10
            p.translate(plane_x, plane_y)
            p.rotate(angle)

            path = QPainterPath()
            s = plane_size
            path.moveTo(s, 0)       # nose tip
            path.lineTo(-s * 0.6, -s * 0.5)   # top wing
            path.lineTo(-s * 0.2, 0)
            path.lineTo(-s * 0.6, s * 0.5)    # bottom wing
            path.closeSubpath()

            p.setPen(QPen(QColor(0, 180, 255, alpha), 1.5))
            p.setBrush(QColor(30, 80, 200, alpha // 2))
            p.drawPath(path)

            # Glow around plane
            g = QRadialGradient(0, 0, plane_size * 2)
            g.setColorAt(0.0, QColor(0, 140, 255, alpha // 2))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(g)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(-plane_size * 2, -plane_size * 2, plane_size * 4, plane_size * 4))

            p.restore()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            # Trail
            trail_len = 5
            for i in range(trail_len):
                tf = max(0, eased - i * 0.05)
                tx = cx + tf * (w * 0.7)
                ty = cy + 8 - tf * (h * 0.6)
                ta = int(120 * (1 - i / trail_len) * (1 - eased))
                tr = 2 - i * 0.3
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                g2 = QRadialGradient(tx, ty, tr * 3)
                g2.setColorAt(0, QColor(0, 160, 255, ta))
                g2.setColorAt(1, QColor(0, 0, 0, 0))
                p.setBrush(g2)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(tx - tr * 3, ty - tr * 3, tr * 6, tr * 6))

        # Always show face watching the action
        look_x = min(40.0, self._send_tick * 1.5) if t > 25 else 0
        look_y = -5.0
        float_y = 0
        eye_w, eye_h = 42, max(4.0, 76 * 0.6)
        eye_spacing = 80
        eye_y = cy - eye_h / 2.0 + float_y - 38 + look_y
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0 + look_x
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0 + look_x
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._draw_glowing_pill(p, QRectF(lx, eye_y, eye_w, eye_h))
        self._draw_glowing_pill(p, QRectF(rx, eye_y, eye_w, eye_h))

    # ── Thinking / neural network animation ───────────────────────────────

    def _draw_scientist_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._tick
        float_y = math.sin(t * 0.1) * 4.0
        
        # ── Lab Coat ──────────────────────────────────────────────────
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(255, 255, 255, 10))
        coat = QPainterPath()
        coat.moveTo(cx - 60, h)
        coat.lineTo(cx - 30, cy + 20 + float_y)
        coat.lineTo(cx + 30, cy + 20 + float_y)
        coat.lineTo(cx + 60, h)
        p.drawPath(coat)

        # ── Beakers ────────────────────────────────────────────────────
        shake = math.sin(t * 0.4) * 8
        # Left beaker
        p.setPen(QPen(QColor(0, 255, 150, 180), 2))
        p.drawRoundedRect(QRectF(cx - 70, cy + 40 + float_y + shake, 25, 40), 4, 4)
        # Liquid bubbling
        for i in range(3):
            bt = (t + i * 20) % 40
            p.setBrush(QColor(0, 255, 150, int(200 * (1 - bt/40))))
            p.drawEllipse(QRectF(cx - 65 + random.randint(-2,2), cy + 70 + float_y + shake - bt, 4, 4))
            
        # Right beaker
        p.setPen(QPen(QColor(255, 100, 0, 180), 2))
        p.drawRoundedRect(QRectF(cx + 45, cy + 40 + float_y - shake, 25, 40), 4, 4)

        # ── Face ───────────────────────────────────────────────────────
        eye_w, eye_h = 42, 36
        eye_y = cy - 30 + float_y
        p.setBrush(QColor(230, 245, 255))
        self._draw_glowing_pill(p, QRectF(cx - 50, eye_y, eye_w, eye_h), glow_alpha=120)
        self._draw_glowing_pill(p, QRectF(cx + 10, eye_y, eye_w, eye_h), glow_alpha=120)

    def _draw_coder_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._tick
        
        # ── Laptop ────────────────────────────────────────────────────
        p.setPen(QPen(QColor(100, 150, 255, 80), 2))
        p.setBrush(QColor(10, 10, 25, 200))
        # Screen
        p.drawRoundedRect(QRectF(cx - 60, cy + 10, 120, 70), 4, 4)
        # Keyboard base
        p.drawRoundedRect(QRectF(cx - 70, cy + 75, 140, 10), 2, 2)
        
        # ── Screen Content (Lines) ────────────────────────────────────
        p.setPen(QPen(QColor(0, 255, 150, 60), 1))
        for i in range(5):
            line_y = cy + 20 + i * 10
            line_w = random.randint(30, 80)
            p.drawLine(QPointF(cx - 50, line_y), QPointF(cx - 50 + line_w, line_y))

        # ── Coffee ─────────────────────────────────────────────────────
        p.setPen(QPen(QColor(150, 100, 50, 150), 2))
        p.drawRoundedRect(QRectF(cx + 75, cy + 60, 20, 25), 3, 3)
        # Steam
        for i in range(2):
            st = (t + i * 30) % 60
            p.setPen(QPen(QColor(255, 255, 255, int(100 * (1 - st/60))), 1))
            sx = cx + 85 + math.sin(st * 0.2) * 3
            p.drawLine(QPointF(sx, cy + 55 - st/2), QPointF(sx, cy + 50 - st/2))

        # ── Hands Typing ──────────────────────────────────────────────
        if t % 4 < 2:
            p.setBrush(QColor(255, 255, 255, 150))
            p.drawEllipse(QRectF(cx - 40 + random.randint(-10,10), cy + 70, 6, 6))
            p.drawEllipse(QRectF(cx + 30 + random.randint(-10,10), cy + 70, 6, 6))

        # ── Face (Looking down) ───────────────────────────────────────
        self._draw_glowing_pill(p, QRectF(cx - 45, cy - 35, 40, 10))
        self._draw_glowing_pill(p, QRectF(cx + 5, cy - 35, 40, 10))

    def _draw_thinking_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._think_tick
        rx, ry = w * 0.42, h * 0.42

        for ring_r, speed, dashes, width, alpha in [
            (min(w, h) * 0.47, 0.012, 6, 1.2, 35),
            (min(w, h) * 0.38, -0.020, 4, 0.8, 25),
        ]:
            angle_offset = t * speed
            dash_arc = 360.0 / (dashes * 2)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            pen = QPen(QColor(0, 120, 255, alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for d in range(dashes):
                start_angle = int((angle_offset * 180 / math.pi + d * 360 / dashes) * 16)
                p.drawArc(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2),
                          start_angle, int(dash_arc * 16))

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for i, (a, b) in enumerate(_EDGES):
            ax, ay = self._node_xy(a, cx, cy, rx, ry)
            bx, by = self._node_xy(b, cx, cy, rx, ry)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.setPen(QPen(QColor(0, 60, 180, 35), 0.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))
            sig = self._edge_signals[i]
            if sig >= 0:
                sx = ax + (bx - ax) * sig
                sy = ay + (by - ay) * sig
                t0 = max(0, sig - 0.25)
                tx0 = ax + (bx - ax) * t0
                ty0 = ay + (by - ay) * t0
                p.setPen(QPen(QColor(0, 180, 255, 200), 1.5))
                p.drawLine(QPointF(tx0, ty0), QPointF(sx, sy))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                g = QRadialGradient(sx, sy, 6)
                g.setColorAt(0.0, QColor(200, 230, 255, 255))
                g.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QRectF(sx - 6, sy - 6, 12, 12))

        for i, (nx, ny) in enumerate(_NODE_POSITIONS):
            x = cx + nx * rx
            y = cy + ny * ry
            pulse = 0.5 + 0.5 * math.sin(t * 0.1 + self._node_phases[i])
            nr = 4 + 3 * pulse
            alpha = int(120 + 135 * pulse)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            gg = QRadialGradient(x, y, nr * 3.5)
            gg.setColorAt(0.0, QColor(0, 160, 255, alpha))
            gg.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(gg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x - nr * 3.5, y - nr * 3.5, nr * 7, nr * 7))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setBrush(QColor(200, 230, 255, min(255, alpha)))
            p.drawEllipse(QRectF(x - nr / 2, y - nr / 2, nr, nr))

        scan_y = cy - ry + ((t * 1.8) % (ry * 2))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        sg = QLinearGradient(cx - rx, scan_y, cx + rx, scan_y)
        sg.setColorAt(0.0, QColor(0, 0, 0, 0))
        sg.setColorAt(0.5, QColor(0, 160, 255, 30))
        sg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(cx - rx, scan_y - 1, rx * 2, 2), sg)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    # ── paintEvent ────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self._is_thinking:
            # Use mode-specific thinking animation
            if self._mode == "reasoning":
                self._draw_scientist_anim(p, w, h)
            elif self._mode == "coding":
                self._draw_coder_anim(p, w, h)
            else:
                self._draw_thinking_anim(p, w, h)
            return
        if self._is_writing:
            self._draw_writing_anim(p, w, h)
            return
        if self._send_tick >= 0:
            self._draw_sending_anim(p, w, h)
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
            yp = (self._state_tick / 20.0 if self._state_tick < 20
                  else 1.0 - (self._state_tick - 60) / 20.0 if self._state_tick > 60 else 1.0)
            mouth_w = 32 - yp * 10
            mouth_h = 16 + yp * 44
            float_y -= yp * 8.0
        elif self._anim_state == "LOOKING":
            look_x = math.cos(self._tick * 0.03) * 4.0
            look_y = math.sin(self._tick * 0.02) * 2.0

        eye_w = 48
        eye_h = max(4.0, 76 * self._blink_factor)
        eye_spacing = 84
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0 + look_x
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0 + look_x
        eye_y = cy - eye_h / 2.0 + float_y + look_y - 16
        mouth_x = cx - mouth_w / 2.0 + look_x * 1.2
        mouth_y = cy + 34 + float_y + (mouth_h / 2.0 if self._anim_state == "YAWNING" else 0)
        yawn_off = mouth_h if self._anim_state == "YAWNING" else 0

        # ── Idle dispatcher ────────────────────────────────────────────────
        if self._idle_anim == "pulse":
            self._draw_pulse_anim(p, w, h)
        elif self._idle_anim == "orbit":
            self._draw_orbit_anim(p, w, h)
        else:
            self._draw_orb_idle(p, w, h, cx, cy, float_y, look_x, look_y, mouth_w, mouth_h)

    # ── Pulse idle animation ───────────────────────────────────────────────

    def _draw_pulse_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._tick
        # Breathing: 0..1..0 over ~120 ticks
        breath = 0.5 + 0.5 * math.sin(t * 0.052)

        # Radial ripple
        for ring in range(3):
            age = (t + ring * 40) % 120
            r_alpha = int(60 * (1 - age / 120))
            r_radius = 30 + age * 1.4
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            rg = QRadialGradient(cx, cy, r_radius)
            rg.setColorAt(0.0, QColor(99, 102, 241, 0))
            rg.setColorAt(0.7, QColor(99, 102, 241, r_alpha))
            rg.setColorAt(1.0, QColor(99, 102, 241, 0))
            p.setBrush(rg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - r_radius, cy - r_radius, r_radius * 2, r_radius * 2))

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # Eyes breathe: scale with breath
        eye_w = 44 + breath * 8
        eye_h = max(4.0, (20 + breath * 40))
        eye_spacing = 82
        eye_y = cy - eye_h / 2.0 - 16
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0
        glow_alpha = int(80 + breath * 100)
        self._draw_glowing_pill(p, QRectF(lx, eye_y, eye_w, eye_h), glow_alpha=glow_alpha)
        self._draw_glowing_pill(p, QRectF(rx, eye_y, eye_w, eye_h), glow_alpha=glow_alpha)

        # Tiny smile breathes too
        smile_w = 20 + breath * 16
        smile_h = 6 + breath * 8
        smile_x = cx - smile_w / 2.0
        smile_y = cy + 30
        self._draw_glowing_pill(p, QRectF(smile_x, smile_y, smile_w, smile_h), glow_alpha=glow_alpha)

    # ── Orbit idle animation ───────────────────────────────────────────────

    def _draw_orbit_anim(self, p, w, h):
        cx, cy = w / 2.0, h / 2.0
        t = self._tick

        # Draw face (subtle)
        eye_w, eye_h = 46, 28
        eye_spacing = 82
        eye_y = cy - eye_h / 2.0 - 16
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0
        self._draw_glowing_pill(p, QRectF(lx, eye_y, eye_w, eye_h), glow_alpha=70)
        self._draw_glowing_pill(p, QRectF(rx, eye_y, eye_w, eye_h), glow_alpha=70)
        self._draw_glowing_pill(p, QRectF(cx - 16, cy + 32, 32, 12), glow_alpha=50)

        # Orbit path (subtle ellipse)
        orb_rx, orb_ry = 80, 50
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setPen(QPen(QColor(99, 102, 241, 18), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - orb_rx, cy - orb_ry, orb_rx * 2, orb_ry * 2))

        # Satellite
        angle = t * 0.035
        sx = cx + math.cos(angle) * orb_rx
        sy = cy + math.sin(angle) * orb_ry

        # Glow halo
        sg = QRadialGradient(sx, sy, 18)
        sg.setColorAt(0.0, QColor(0, 200, 255, 140))
        sg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(sg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(sx - 18, sy - 18, 36, 36))

        # Core dot
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setBrush(QColor(200, 240, 255, 230))
        p.drawEllipse(QRectF(sx - 4, sy - 4, 8, 8))

        # Trail
        for i in range(1, 6):
            ta = angle - i * 0.08
            tx = cx + math.cos(ta) * orb_rx
            ty = cy + math.sin(ta) * orb_ry
            trail_alpha = int(80 * (1 - i / 6))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            tg = QRadialGradient(tx, ty, 5)
            tg.setColorAt(0, QColor(0, 200, 255, trail_alpha))
            tg.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(tg)
            p.drawEllipse(QRectF(tx - 5, ty - 5, 10, 10))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    # ── Classic Orb idle ───────────────────────────────────────────────────

    def _draw_orb_idle(self, p, w, h, cx, cy, float_y, look_x, look_y, mouth_w, mouth_h):
        eye_w = 48
        eye_h = max(4.0, 76 * self._blink_factor)
        eye_spacing = 84
        lx = cx - eye_spacing / 2.0 - eye_w / 2.0 + look_x
        rx = cx + eye_spacing / 2.0 - eye_w / 2.0 + look_x
        eye_y = cy - eye_h / 2.0 + float_y + look_y - 16
        mouth_x = cx - mouth_w / 2.0 + look_x * 1.2
        mouth_y = cy + 34 + float_y + (mouth_h / 2.0 if self._anim_state == "YAWNING" else 0)
        yawn_off = mouth_h if self._anim_state == "YAWNING" else 0
        self._draw_glowing_pill(p, QRectF(lx, eye_y, eye_w, eye_h))
        self._draw_glowing_pill(p, QRectF(rx, eye_y, eye_w, eye_h))
        self._draw_glowing_pill(p, QRectF(mouth_x, mouth_y - yawn_off, mouth_w, mouth_h))
