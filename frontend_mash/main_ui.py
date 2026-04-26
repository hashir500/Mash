"""
frontend_mash/main_ui.py
========================
Mash – Virtual Desktop Pet UI

A frameless, translucent, always-on-top desktop widget that:
  • Connects to the LiveKit room as a plain participant (camera/mic passthrough).
  • Listens to the LiveKit data channel for state / stat events from the backend brain.
  • Renders an animated glassmorphic avatar whose appearance reacts to every state.
  • Supports click-to-wake, drag-to-move, right-click context menu.
  • Emits microphone audio into the room so the backend STT can hear the user.

Run standalone:
    python frontend_mash/main_ui.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path

# ── path bootstrap ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # type: ignore
load_dotenv(PROJECT_ROOT / ".env")

# ── shared ────────────────────────────────────────────────────────────────────
from shared.events import (
    DATA_TOPIC, ROOM_NAME,
    STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING, STATE_SLEEPING,
    EVT_STATE_CHANGE, EVT_STAT_UPDATE, EVT_TRANSCRIPT, EVT_GREETING, EVT_HEARTBEAT,
    STAT_MAX,
)

# ── Qt ────────────────────────────────────────────────────────────────────────
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QObject, QPoint, QRectF, QPointF,
    QThread, pyqtSlot,
)
from PyQt6.QtGui import (
    QPainter, QColor, QRadialGradient, QLinearGradient,
    QFont, QPainterPath, QPen, QBrush, QIcon, QPixmap,
    QFontDatabase,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect,
)

# ── LiveKit ───────────────────────────────────────────────────────────────────
from livekit import rtc, api

logger = logging.getLogger("mash.ui")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# Colour palette (HSL-curated)
# ─────────────────────────────────────────────────────────────────────────────
PALETTE = {
    STATE_IDLE:      QColor(120, 200, 255, 200),   # cool sky-blue
    STATE_LISTENING: QColor(100, 255, 180, 220),   # mint-green
    STATE_THINKING:  QColor(200, 150, 255, 210),   # soft violet
    STATE_SPEAKING:  QColor(255, 200,  80, 230),   # warm amber
    STATE_SLEEPING:  QColor( 80, 100, 140, 160),   # muted navy
}

GLOW = {
    STATE_IDLE:      QColor(120, 200, 255,  60),
    STATE_LISTENING: QColor(100, 255, 180,  80),
    STATE_THINKING:  QColor(200, 150, 255,  70),
    STATE_SPEAKING:  QColor(255, 200,  80,  90),
    STATE_SLEEPING:  QColor( 80, 100, 140,  40),
}

GLASS_BG    = QColor(15, 15, 25, 180)
GLASS_RING  = QColor(255, 255, 255, 40)
WHITE_ALPHA = QColor(255, 255, 255, 15)

ORB_RADIUS  = 80          # px – orb circle radius
WIN_SIZE    = 240         # px – total window size
TRAY_SIZE   = 22          # px – tray icon size


# ─────────────────────────────────────────────────────────────────────────────
# SignalBus – thread-safe bridge between asyncio LiveKit thread and Qt main thread
# ─────────────────────────────────────────────────────────────────────────────
class SignalBus(QObject):
    state_changed  = pyqtSignal(str)          # new state string
    stats_updated  = pyqtSignal(float, float) # energy, mood
    transcript_rx  = pyqtSignal(str, str)     # role, text
    connected      = pyqtSignal()
    disconnected   = pyqtSignal()
    error          = pyqtSignal(str)


bus = SignalBus()


# ─────────────────────────────────────────────────────────────────────────────
# LiveKit client thread
# ─────────────────────────────────────────────────────────────────────────────
class LiveKitWorker(QThread):
    """
    Runs the asyncio event loop in a background QThread.
    Connects to the LiveKit room, publishes microphone audio,
    and forwards data-channel messages to the Qt signal bus.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._room: rtc.Room | None = None
        self._running = False

    # ── public API (called from Qt thread) ────────────────────────────────────
    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── QThread.run – executed in background thread ───────────────────────────
    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._running = True
        try:
            self._loop.run_until_complete(self._connect())
        except Exception as exc:
            logger.error("LiveKit worker error: %s", exc, exc_info=True)
            bus.error.emit(str(exc))
        finally:
            self._loop.close()

    # ── async coroutine ───────────────────────────────────────────────────────
    async def _connect(self) -> None:
        url     = os.environ["LIVEKIT_URL"]
        api_key = os.environ["LIVEKIT_API_KEY"]
        secret  = os.environ["LIVEKIT_API_SECRET"]

        # Generate a participant token for the UI client
        token = (
            api.AccessToken(api_key, secret)
            .with_identity("mash-ui")
            .with_name("Mash Desktop UI")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=ROOM_NAME,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
            .to_jwt()
        )

        room = rtc.Room()
        self._room = room

        # ── data channel handler ──────────────────────────────────────────────
        @room.on("data_received")
        def _on_data(dp: rtc.DataPacket) -> None:
            try:
                msg = json.loads(dp.data.decode())
                evt_type = msg.get("type")
                payload  = msg.get("payload", {})

                if evt_type == EVT_STATE_CHANGE:
                    bus.state_changed.emit(payload.get("state", STATE_IDLE))

                elif evt_type == EVT_STAT_UPDATE:
                    bus.stats_updated.emit(
                        float(payload.get("energy", 80)),
                        float(payload.get("mood",   75)),
                    )

                elif evt_type == EVT_TRANSCRIPT:
                    bus.transcript_rx.emit(
                        payload.get("role", "agent"),
                        payload.get("text", ""),
                    )

                elif evt_type == EVT_GREETING:
                    bus.connected.emit()

                # EVT_HEARTBEAT silently ignored

            except Exception as exc:
                logger.warning("data_received parse error: %s", exc)

        @room.on("connected")
        def _on_connected() -> None:
            logger.info("LiveKit room connected")
            bus.connected.emit()

        @room.on("disconnected")
        def _on_disconnected(*_) -> None:
            logger.info("LiveKit room disconnected")
            bus.disconnected.emit()

        @room.on("track_subscribed")
        def _on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info("Subscribed to agent audio track!")
                asyncio.create_task(self._play_audio_track(track))

        # ── connect ───────────────────────────────────────────────────────────
        logger.info("Connecting to LiveKit room '%s'…", ROOM_NAME)
        await room.connect(url, token)

        # ── publish microphone audio ──────────────────────────────────────────
        try:
            source  = rtc.AudioSource(sample_rate=16000, num_channels=1)
            mic_trk = rtc.LocalAudioTrack.create_audio_track("mash-mic", source)
            opts    = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(mic_trk, opts)

            # Stream system microphone via sounddevice → LiveKit
            await self._stream_mic(source)
        except Exception as exc:
            logger.error("Mic publish error: %s", exc)

        # ── keep alive until stopped ──────────────────────────────────────────
        while self._running:
            await asyncio.sleep(0.5)

        await room.disconnect()

    async def _play_audio_track(self, track: rtc.AudioTrack) -> None:
        """Pulls audio frames from the LiveKit remote track and plays them via sounddevice."""
        import copy
        audio_stream = rtc.AudioStream(track)

        def _play_thread():
            import sounddevice as sd
            import numpy as np

            # Block until we get the first frame to find the sample rate
            try:
                first_event_fut = asyncio.run_coroutine_threadsafe(audio_stream.__anext__(), self._loop)
                first_event = first_event_fut.result(timeout=10)
            except Exception as exc:
                logger.error("Failed to get first audio frame: %s", exc)
                return

            frame = first_event.frame
            
            try:
                with sd.OutputStream(samplerate=frame.sample_rate, channels=frame.num_channels, dtype='int16') as stream:
                    stream.write(np.frombuffer(frame.data, dtype=np.int16))
                    while self._running:
                        try:
                            fut = asyncio.run_coroutine_threadsafe(audio_stream.__anext__(), self._loop)
                            # Wait for next frame (blocks thread but NOT asyncio loop!)
                            event = fut.result()
                            stream.write(np.frombuffer(event.frame.data, dtype=np.int16))
                        except StopAsyncIteration:
                            break
                        except Exception as inner_exc:
                            logger.error("Audio stream iteration error: %s", inner_exc)
                            break
            except Exception as exc:
                logger.error("OutputStream failed: %s", exc)

        threading.Thread(target=_play_thread, daemon=True).start()

    async def _stream_mic(self, source: rtc.AudioSource) -> None:
        """Push system mic samples into the LiveKit audio source."""
        import sounddevice as sd  # type: ignore
        import numpy as np

        SAMPLE_RATE  = 16000
        FRAME_MS     = 10           # 10 ms frames
        SAMPLES      = SAMPLE_RATE * FRAME_MS // 1000

        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if status:
                logger.debug("mic status: %s", status)
            pcm = (indata[:, 0] * 32767).astype(np.int16)
            frame = rtc.AudioFrame(
                data=pcm.tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=len(pcm),
            )
            asyncio.run_coroutine_threadsafe(source.capture_frame(frame), loop)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=SAMPLES,
            callback=callback,
        ):
            while self._running:
                await asyncio.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
# Orb Canvas – custom-painted animated orb
# ─────────────────────────────────────────────────────────────────────────────
class OrbCanvas(QWidget):
    """
    Renders the Mash avatar as an animated glowing orb.
    All drawing is done via QPainter for smooth, GPU-friendly compositing.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(WIN_SIZE, WIN_SIZE)

        self._state      = STATE_IDLE
        self._energy     = 80.0
        self._mood       = 75.0
        self._phase      = 0.0          # animation phase (radians)
        self._speak_amp  = 0.0          # speaking pulse amplitude
        self._blink_open = 1.0          # eye-open fraction 0→1
        self._blink_t    = 0.0          # blink timer

        # ── animation tick ────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60 fps

        # ── blink timer ───────────────────────────────────────────────────────
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._trigger_blink)
        self._blink_timer.start(3500)

        self._t0 = time.time()

    # ── public setters (called via Qt signals) ────────────────────────────────
    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_stats(self, energy: float, mood: float) -> None:
        self._energy = energy
        self._mood   = mood
        self.update()

    # ── animation ─────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        dt = time.time() - self._t0
        speed = {
            STATE_IDLE:      0.8,
            STATE_LISTENING: 1.4,
            STATE_THINKING:  2.0,
            STATE_SPEAKING:  2.8,
            STATE_SLEEPING:  0.3,
        }.get(self._state, 1.0)
        self._phase = (dt * speed) % (2 * math.pi)

        # Speaking pulse decay
        if self._state == STATE_SPEAKING:
            self._speak_amp = min(1.0, self._speak_amp + 0.08)
        else:
            self._speak_amp = max(0.0, self._speak_amp - 0.05)

        # Blink animation
        if self._blink_t > 0:
            self._blink_t = max(0.0, self._blink_t - 0.07)
            self._blink_open = self._blink_t / 1.0
        else:
            self._blink_open = 1.0

        self.update()

    def _trigger_blink(self) -> None:
        if self._state != STATE_SLEEPING:
            self._blink_t = 1.0

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = WIN_SIZE / 2
        cy = WIN_SIZE / 2

        color = PALETTE.get(self._state, PALETTE[STATE_IDLE])
        glow  = GLOW.get(self._state,   GLOW[STATE_IDLE])

        # ── outer glow ────────────────────────────────────────────────────────
        glow_r = ORB_RADIUS + 28 + 8 * math.sin(self._phase)
        grad_glow = QRadialGradient(QPointF(cx, cy), glow_r)
        grad_glow.setColorAt(0.0, glow)
        grad_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad_glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # ── floating offset ───────────────────────────────────────────────────
        float_y = cy + 6 * math.sin(self._phase)

        # ── orb body (radial gradient – glass look) ───────────────────────────
        orb_r = ORB_RADIUS + (4 * math.sin(self._phase * 2) if self._state == STATE_SPEAKING else 0)
        orb_r *= (0.6 + 0.4 * (self._energy / 100))  # shrinks when sleepy

        grad_orb = QRadialGradient(QPointF(cx - orb_r * 0.3, float_y - orb_r * 0.3), orb_r * 1.2)
        light_color = QColor(
            min(255, color.red()   + 60),
            min(255, color.green() + 60),
            min(255, color.blue()  + 60),
            230,
        )
        grad_orb.setColorAt(0.0, light_color)
        grad_orb.setColorAt(0.5, color)
        dark_color = QColor(
            max(0, color.red()   - 40),
            max(0, color.green() - 40),
            max(0, color.blue()  - 40),
            200,
        )
        grad_orb.setColorAt(1.0, dark_color)
        p.setBrush(QBrush(grad_orb))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, float_y), orb_r, orb_r)

        # ── glass specular highlight ──────────────────────────────────────────
        spec_r = orb_r * 0.38
        grad_spec = QRadialGradient(QPointF(cx - orb_r * 0.25, float_y - orb_r * 0.28), spec_r)
        grad_spec.setColorAt(0.0, QColor(255, 255, 255, 160))
        grad_spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(grad_spec))
        p.drawEllipse(QPointF(cx - orb_r * 0.25, float_y - orb_r * 0.28), spec_r, spec_r)

        # ── rim ring ─────────────────────────────────────────────────────────
        p.setBrush(Qt.BrushStyle.NoBrush)
        rim_color = QColor(255, 255, 255, 50)
        p.setPen(QPen(rim_color, 1.5))
        p.drawEllipse(QPointF(cx, float_y), orb_r, orb_r)

        # ── face ─────────────────────────────────────────────────────────────
        self._draw_face(p, cx, float_y, orb_r)

        # ── speaking waveform ring ────────────────────────────────────────────
        if self._speak_amp > 0.01:
            self._draw_speak_ring(p, cx, float_y, orb_r)

        # ── stat bars ─────────────────────────────────────────────────────────
        self._draw_stat_bars(p)

        # ── state label ───────────────────────────────────────────────────────
        self._draw_label(p)

        p.end()

    def _draw_face(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Draw two eyes and a mouth that change with state."""
        eye_color = QColor(255, 255, 255, 220)
        eye_size  = r * 0.16
        eye_y     = cy - r * 0.15

        # Blink squish
        blink_sy = self._blink_open

        if self._state == STATE_SLEEPING:
            # Closed crescent eyes + zzz
            p.setPen(QPen(eye_color, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for ex in [cx - r * 0.28, cx + r * 0.28]:
                p.drawArc(
                    QRectF(ex - eye_size, eye_y - eye_size * 0.5,
                           eye_size * 2, eye_size),
                    0, 180 * 16,
                )
            # ZZZ text
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            f = QFont("Sans Serif", int(r * 0.18))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(cx + r * 0.1, cy - r * 0.55, r * 0.6, r * 0.3),
                       Qt.AlignmentFlag.AlignCenter, "z z z")
        else:
            # Normal round eyes
            p.setBrush(QBrush(eye_color))
            p.setPen(Qt.PenStyle.NoPen)
            for ex in [cx - r * 0.28, cx + r * 0.28]:
                ey_h = eye_size * blink_sy
                ey_off = eye_size * (1 - blink_sy) * 0.5
                p.drawEllipse(
                    QRectF(ex - eye_size, eye_y - ey_h * 0.5 + ey_off,
                           eye_size * 2, max(0.5, ey_h * 2)),
                )

            # Tiny pupil
            pupil_color = QColor(20, 20, 40, 200)
            p.setBrush(QBrush(pupil_color))
            for ex in [cx - r * 0.28, cx + r * 0.28]:
                ps = eye_size * 0.55 * blink_sy
                p.drawEllipse(QPointF(ex, eye_y), ps, ps)

        # ── mouth ─────────────────────────────────────────────────────────────
        mouth_y  = cy + r * 0.30
        mouth_w  = r * 0.55
        mouth_h  = r * 0.20

        p.setPen(QPen(eye_color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        if self._state == STATE_SPEAKING:
            # Open / wavy mouth
            path.moveTo(cx - mouth_w * 0.5, mouth_y)
            cp1x = cx - mouth_w * 0.2
            cp2x = cx + mouth_w * 0.2
            amp  = mouth_h * (0.5 + 0.5 * math.sin(self._phase * 3))
            path.cubicTo(cp1x, mouth_y + amp, cp2x, mouth_y - amp, cx + mouth_w * 0.5, mouth_y)
        elif self._state == STATE_THINKING:
            # Flat / pensive
            path.moveTo(cx - mouth_w * 0.35, mouth_y)
            path.lineTo(cx + mouth_w * 0.35, mouth_y)
        elif self._state in (STATE_IDLE, STATE_LISTENING):
            # Slight smile
            path.moveTo(cx - mouth_w * 0.4, mouth_y)
            path.cubicTo(
                cx - mouth_w * 0.1, mouth_y + mouth_h,
                cx + mouth_w * 0.1, mouth_y + mouth_h,
                cx + mouth_w * 0.4, mouth_y,
            )
        else:
            # Sleeping – small squiggle
            path.moveTo(cx - mouth_w * 0.25, mouth_y)
            path.cubicTo(
                cx - mouth_w * 0.05, mouth_y + mouth_h * 0.5,
                cx + mouth_w * 0.05, mouth_y - mouth_h * 0.5,
                cx + mouth_w * 0.25, mouth_y,
            )
        p.drawPath(path)

    def _draw_speak_ring(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Pulsing ring around the orb when speaking."""
        n_rings = 3
        for i in range(n_rings):
            offset  = i * 12
            alpha   = int(80 * self._speak_amp * (1 - i / n_rings))
            wave_r  = r + offset + 6 * math.sin(self._phase + i * 1.2)
            ring_c  = QColor(255, 200, 80, alpha)
            p.setPen(QPen(ring_c, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), wave_r, wave_r)

    def _draw_stat_bars(self, p: QPainter) -> None:
        """Small energy/mood bar strip at the bottom of the window."""
        bw    = 90       # bar total width
        bh    = 5        # bar height
        x0    = (WIN_SIZE - bw) / 2
        y_e   = WIN_SIZE - 28
        y_m   = WIN_SIZE - 18
        gap   = 3        # gap between rail and fill

        for (y, val, col) in [
            (y_e, self._energy, QColor(100, 220, 255, 180)),
            (y_m, self._mood,   QColor(255, 140, 200, 180)),
        ]:
            # Rail
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 30)))
            p.drawRoundedRect(QRectF(x0, y, bw, bh), bh / 2, bh / 2)
            # Fill
            fill_w = max(bh, bw * (val / STAT_MAX))
            p.setBrush(QBrush(col))
            p.drawRoundedRect(QRectF(x0, y, fill_w, bh), bh / 2, bh / 2)

    def _draw_label(self, p: QPainter) -> None:
        """Tiny state label below the stat bars."""
        labels = {
            STATE_IDLE:      "idle",
            STATE_LISTENING: "listening…",
            STATE_THINKING:  "thinking…",
            STATE_SPEAKING:  "speaking",
            STATE_SLEEPING:  "sleeping…",
        }
        text = labels.get(self._state, "")
        p.setPen(QPen(QColor(255, 255, 255, 100)))
        f = QFont("Sans Serif", 8)
        p.setFont(f)
        p.drawText(QRectF(0, WIN_SIZE - 10, WIN_SIZE, 10),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)


# ─────────────────────────────────────────────────────────────────────────────
# Transcript overlay
# ─────────────────────────────────────────────────────────────────────────────
class TranscriptBubble(QWidget):
    """A small chat bubble that pops up and fades when text arrives."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self._text = ""
        self._alpha = 0
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade)
        self.setFixedSize(260, 80)

    def show_text(self, role: str, text: str) -> None:
        prefix = "🎙 " if role == "user" else "💬 "
        self._text = prefix + text[:120] + ("…" if len(text) > 120 else "")
        self._alpha = 240
        self._fade_timer.stop()
        QTimer.singleShot(2500, self._start_fade)
        self.show()
        self.update()

    def _start_fade(self) -> None:
        self._fade_timer.start(40)

    def _fade(self) -> None:
        self._alpha = max(0, self._alpha - 12)
        self.update()
        if self._alpha == 0:
            self._fade_timer.stop()
            self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(15, 15, 30, int(self._alpha * 0.85))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(255, 255, 255, int(self._alpha * 0.3)), 1))
        p.drawRoundedRect(self.rect(), 12, 12)
        text_c = QColor(220, 220, 255, self._alpha)
        p.setPen(QPen(text_c))
        f = QFont("Sans Serif", 9)
        p.setFont(f)
        p.drawText(self.rect().adjusted(10, 8, -10, -8),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter |
                   Qt.TextFlag.TextWordWrap,
                   self._text)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────
class MashWindow(QWidget):
    """
    Frameless, transparent, always-on-top desktop widget.
    Contains the OrbCanvas and wires LiveKit signals to it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._drag_pos: QPoint | None = None

        # ── window flags ──────────────────────────────────────────────────────
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(WIN_SIZE, WIN_SIZE)
        self.setWindowTitle("Mash")

        # Icon
        icon_path = Path(__file__).parent / "icon.jpeg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── orb canvas ────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._orb = OrbCanvas(self)
        layout.addWidget(self._orb)

        # ── transcript bubble ─────────────────────────────────────────────────
        self._bubble = TranscriptBubble()

        # ── tray icon ─────────────────────────────────────────────────────────
        self._setup_tray(icon_path)

        # ── livekit worker ────────────────────────────────────────────────────
        self._worker = LiveKitWorker(self)
        self._worker.start()

        # ── connect signals ───────────────────────────────────────────────────
        bus.state_changed.connect(self._on_state)
        bus.stats_updated.connect(self._on_stats)
        bus.transcript_rx.connect(self._on_transcript)
        bus.connected.connect(self._on_connected)
        bus.disconnected.connect(self._on_disconnected)
        bus.error.connect(self._on_error)

        # ── position: bottom-right corner ─────────────────────────────────────
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - WIN_SIZE - 20, screen.bottom() - WIN_SIZE - 20)
        self.show()

    # ── tray ──────────────────────────────────────────────────────────────────
    def _setup_tray(self, icon_path: Path) -> None:
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        menu.addAction("Show / Hide",  self._toggle_visible)
        menu.addAction("Reset position", self._reset_position)
        menu.addSeparator()
        menu.addAction("Quit Mash", QApplication.instance().quit)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("Mash – virtual desktop agent")
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

    def _toggle_visible(self) -> None:
        self.setVisible(not self.isVisible())

    def _reset_position(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - WIN_SIZE - 20, screen.bottom() - WIN_SIZE - 20)

    @pyqtSlot(QSystemTrayIcon.ActivationReason)
    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()

    # ── drag to move ──────────────────────────────────────────────────────────
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev) -> None:   # noqa: N802
        if self._drag_pos and ev.buttons() & Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            self._reposition_bubble()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._drag_pos = None

    # ── signal handlers ───────────────────────────────────────────────────────
    @pyqtSlot(str)
    def _on_state(self, state: str) -> None:
        if state == STATE_LISTENING and self._orb._state != STATE_LISTENING:
            self._play_beep()
        self._orb.set_state(state)

    @pyqtSlot(float, float)
    def _on_stats(self, energy: float, mood: float) -> None:
        self._orb.set_stats(energy, mood)

    @pyqtSlot(str, str)
    def _on_transcript(self, role: str, text: str) -> None:
        self._bubble.show_text(role, text)
        self._reposition_bubble()

    @pyqtSlot()
    def _on_connected(self) -> None:
        logger.info("UI: connected to Mash brain")
        self._orb.set_state(STATE_IDLE)

    @pyqtSlot()
    def _on_disconnected(self) -> None:
        logger.info("UI: disconnected from Mash brain")
        self._orb.set_state(STATE_SLEEPING)

    @pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        logger.error("LiveKit error: %s", msg)

    def _reposition_bubble(self) -> None:
        gp = self.mapToGlobal(QPoint(0, 0))
        self._bubble.move(gp.x() - 270, gp.y() + 40)

    def _play_beep(self) -> None:
        """Plays a short, pleasant 'ready' chime using sounddevice."""
        import threading
        def _beep_thread():
            try:
                import sounddevice as sd
                import numpy as np
                fs = 44100
                duration = 0.15
                f = 880.0
                samples = (np.sin(2 * np.pi * np.arange(fs * duration) * f / fs)).astype(np.float32)
                # Quick fade in/out
                fade = int(fs * 0.05)
                samples[:fade] *= np.linspace(0, 1, fade)
                samples[-fade:] *= np.linspace(1, 0, fade)
                sd.play(samples * 0.04, fs)
            except Exception as exc:
                logger.debug("beep failed: %s", exc)
        threading.Thread(target=_beep_thread, daemon=True).start()

    # ── close → stop worker ──────────────────────────────────────────────────
    def closeEvent(self, ev) -> None:  # noqa: N802
        self._worker.stop()
        self._worker.wait(3000)
        ev.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Mash")
    app.setQuitOnLastWindowClosed(False)   # keep running via tray

    # Load system font
    QFontDatabase.addApplicationFont("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

    win = MashWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
