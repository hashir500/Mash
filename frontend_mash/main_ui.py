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

from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QUrl

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
# Video Avatar – plays MP4s mapped to states
# ─────────────────────────────────────────────────────────────────────────────
class VideoAvatar(QWidget):
    """
    Renders the Mash avatar using QtMultimedia, styled like a neon rectangular screen.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Rectangular dimension
        w, h = 260, 200
        self.setFixedSize(w, h)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6) # space for border

        # Video surface
        self.video_widget = QVideoWidget()
        layout.addWidget(self.video_widget)

        # Style the container for rounded neon green look
        self.setStyleSheet("""
            VideoAvatar {
                background-color: transparent;
                border: 4px solid #0df524;
                border-radius: 16px;
            }
            QVideoWidget {
                background-color: black;
                border-radius: 12px;
            }
        """)

        # Backend player
        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.video_widget)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)

        self._state = STATE_IDLE
        self.video_dir = PROJECT_ROOT / "videos"
        
        # State mappings to mp4
        self.state_map = {
            STATE_IDLE:      "default.mp4",
            STATE_LISTENING: "uwu.mp4",
            STATE_THINKING:  "distracted.mp4",
            STATE_SPEAKING:  "smile.mp4",
            STATE_SLEEPING:  "sleepy.mp4",
        }

    def set_state(self, state: str) -> None:
        if self._state == state and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return # already playing the correct state

        self._state = state
        
        # Dynamically change the border color!
        border_colors = {
            STATE_IDLE:      "#0df524",  # Neon Green
            STATE_LISTENING: "#00bfff",  # Deep Sky Blue
            STATE_THINKING:  "#a020f0",  # Purple
            STATE_SPEAKING:  "#ffff00",  # Bright Yellow
            STATE_SLEEPING:  "#404080",  # Dark Blueish Gray
        }
        color = border_colors.get(state, "#0df524")
        self.setStyleSheet(f"""
            VideoAvatar {{
                background-color: transparent;
                border: 4px solid {color};
                border-radius: 16px;
            }}
            QVideoWidget {{
                background-color: black;
                border-radius: 12px;
            }}
        """)

        filename = self.state_map.get(state, "default.mp4")
        vpath = self.video_dir / filename
        
        if vpath.exists():
            self.player.setSource(QUrl.fromLocalFile(str(vpath)))
            self.player.play()
        else:
            logger.warning("Missing video: %s", vpath)

    def set_stats(self, energy: float, mood: float) -> None:
        # We don't render progress bars on top of the videos per the design
        pass

    def paintEvent(self, _event) -> None:
        # Need to implement paintEvent just so stylesheets properly apply on QWidget subclasses
        from PyQt6.QtWidgets import QStyleOption, QStyle
        from PyQt6.QtGui import QPainter
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)



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
        self.setFixedSize(260, 200)
        self.setWindowTitle("Mash")

        # Icon
        icon_path = Path(__file__).parent / "icon.jpeg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── video canvas ──────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._orb = VideoAvatar(self)
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
        self.move(screen.right() - 260 - 20, screen.bottom() - 200 - 20)
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
        self.move(screen.right() - 260 - 20, screen.bottom() - 200 - 20)

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
