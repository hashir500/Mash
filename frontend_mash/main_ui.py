import sys
import os
import json
import math
import queue
import struct
import asyncio
import threading
import subprocess
import numpy as np
import time
from dotenv import load_dotenv


from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QProgressBar, QFrame, QWidget, QGraphicsView, QGraphicsScene
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QPoint, QUrl, QRectF, QSizeF
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtGui import QRegion, QPainterPath

from livekit import rtc, api

load_dotenv()


class AudioPlayer:
    """
    Robust audio output via aplay subprocess + dedicated writer thread.

    Design:
    - LiveKit async thread calls write() → enqueues frame (non-blocking, ~1μs).
    - Dedicated writer thread dequeues frames and writes to aplay stdin.
    - No silence pump, no chunk splitting — frames are written as-is.
    - ALSA's internal buffer (--buffer-size) absorbs network jitter.
    """

    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.is_playing = False
        self._q = queue.Queue(maxsize=500)
        self._running = False
        self._proc = None
        self._start(sample_rate, channels)

    def _start(self, sample_rate: int, channels: int):
        cmd = [
            'aplay',
            '-t', 'raw',
            '-f', 'S16_LE',
            '-r', str(sample_rate),
            '-c', str(channels),
            '-',
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                bufsize=0,   # Unbuffered — each write() hits the OS pipe immediately
            )
            self._running = True
            threading.Thread(
                target=self._writer_loop, daemon=True, name='audio-writer'
            ).start()
            print("DEBUG: AudioPlayer started via aplay.")
        except FileNotFoundError:
            print("ERROR: aplay not found. Install: sudo apt install alsa-utils")

    def _writer_loop(self):
        """Write queued audio frames to aplay stdin. Block until data arrives."""
        last_write_time = 0
        while self._running and self._proc and self._proc.poll() is None:
            try:
                data = self._q.get(timeout=0.1)
                self._proc.stdin.write(data)
                last_write_time = time.time()
                self.is_playing = True
            except queue.Empty:
                if time.time() - last_write_time > 1.2:
                    self.is_playing = False
                continue
            except (BrokenPipeError, OSError, ValueError) as e:
                print(f"DEBUG: aplay pipe broken ({e}), restarting...")
                self.is_playing = False
                try:
                    if self._proc:
                        self._proc.kill()
                except:
                    pass
                # Give the OS a tiny moment to release the device
                time.sleep(0.1)
                self._start(24000, 1)
                break # the new _start() call spawns a new thread, so exit this old thread

    def write(self, data: bytes):
        """Enqueue audio frame as-is. Non-blocking — drops frame if queue full."""
        try:
            self._q.put_nowait(data)
        except queue.Full:
            pass

    def beep(self, freq: float = 440, duration: float = 0.3, amplitude: int = 8000):
        n = int(24000 * duration)
        samples = [int(amplitude * math.sin(2 * math.pi * freq * i / 24000)) for i in range(n)]
        self.write(struct.pack(f'<{n}h', *samples))

    def close(self):
        self._running = False
        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass

class WorkerSignals(QObject):
    energy_updated = pyqtSignal(int)
    status_updated = pyqtSignal(str)
    expression_updated = pyqtSignal(str)
    connected = pyqtSignal()
    connection_failed = pyqtSignal(str)
    # Thread-safe bridge: background thread emits this, GUI thread writes to audio sink
    audio_frame = pyqtSignal(bytes)

class LiveKitThread(threading.Thread):
    def __init__(self, signals: WorkerSignals, audio_player):
        super().__init__()
        self.signals = signals
        self.audio_player = audio_player  # Write directly — no Qt signal queue
        self.room = None
        self.url = os.getenv("LIVEKIT_URL")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")
        
        if api_key and api_secret:
            import uuid
            self._ui_id = f"mash-ui-{uuid.uuid4().hex[:6]}"
            self.token = api.AccessToken(api_key, api_secret) \
                .with_identity(self._ui_id) \
                .with_name("Mash UI") \
                .with_grants(api.VideoGrants(room_join=True, can_publish=True, can_publish_data=True, room="mash-vRest-9")) \
                .to_jwt()
        else:
            self.token = ""
        
        self.active = True
        self.loop = None
        self.mic_muted = False

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.room = rtc.Room(loop=self.loop)
        self._current_track = None
        self._stream_task = None
        
        @self.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.RemoteTrack, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"Subscribed to audio track: {track.sid}")
                self.signals.status_updated.emit("ready")
                # Immediately mute mic so he doesn't hear his own diagnostic beep
                self.mic_muted = True
                self.audio_player.beep(880, 0.2) # Diagnostic high-pitch beep
                self._current_track = track
                if self._stream_task:
                    self._stream_task.cancel()
                self._stream_task = asyncio.run_coroutine_threadsafe(self._stream_to_device(), self.loop)

        @self.room.on("data_received")
        def on_data_received(data_pkt: rtc.DataPacket):
            try:
                msg = json.loads(data_pkt.data.decode("utf-8"))
                if msg.get("type") == "agent_state":
                    state_data = msg.get("data", {})
                    if "energy" in state_data:
                        self.signals.energy_updated.emit(state_data["energy"])
                    if "status" in state_data:
                        self.signals.status_updated.emit(state_data["status"])
                elif msg.get("type") == "agent_expression":
                    expr_data = msg.get("data", {})
                    if "expression" in expr_data:
                        self.signals.expression_updated.emit(expr_data["expression"])
                elif msg.get("type") == "mute_mic":
                    print("DEBUG: MASH VOICE DETECTED -> NUCLEAR MUTE")
                    if hasattr(self, "_mic_track"):
                        self._mic_track.mute()
                    self.mic_muted = True
                elif msg.get("type") == "unmute_mic":
                    print("DEBUG: MASH VOICE ENDED -> NUCLEAR UNMUTE")
                    if hasattr(self, "_mic_track"):
                        self._mic_track.unmute()
                    self.mic_muted = False
            except Exception as e:
                print(f"Error parsing data: {e}")

        try:
            self.loop.run_until_complete(self._connect())
            self.loop.run_forever()
        except Exception as e:
            print(f"Loop error: {e}")
        finally:
            self.loop.close()

    async def _connect(self):
        if not self.url or not self.token:
            self.signals.connection_failed.emit("Missing Credentials.")
            return

        try:
            await self.room.connect(self.url, self.token)
            print(f"DEBUG: Frontend connected to room: {self.room.name}")
            # Microphone (Native SDK)
            try:
                self.devices = rtc.MediaDevices()
                self.mic_handle = self.devices.open_input()
                self._mic_track = rtc.LocalAudioTrack.create_audio_track("microphone", self.mic_handle.source)
                
                publish_opts = rtc.TrackPublishOptions()
                publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
                await self.room.local_participant.publish_track(self._mic_track, publish_opts)
                print("Microphone activated and publishing natively.")
                # Start the silence watcher
                asyncio.create_task(self._monitor_playback())
            except Exception as mic_e:
                print(f"Mic failed: {mic_e}")

                
            self.signals.connected.emit()
        except Exception as e:
            self.signals.connection_failed.emit(str(e))

    async def _stream_to_device(self):
        print("DEBUG: Listening to agent...")
        try:
            stream = rtc.AudioStream.from_track(track=self._current_track, sample_rate=24000)
            async for event in stream:
                if not self.active: break
                if event.frame:
                    # Synchronous OS-level block BEFORE pushing to speakers
                    if not self.mic_muted:
                        os.system("amixer sset Capture nocap >/dev/null 2>&1; wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 1 >/dev/null 2>&1")
                        self.mic_muted = True
                        
                    self.audio_player.write(bytes(event.frame.data))
                    # Auto-mute when audio flows
                    if hasattr(self, "_mic_track") and not self.mic_muted:
                         self._mic_track.mute()
                         self.mic_muted = True
            
            # Fallback unmute
            if hasattr(self, "_mic_track"):
                self._mic_track.unmute()
        except Exception as e:
            print(f"Stream error: {e}")
            
    async def _monitor_playback(self):
        """Watch for silence and unmute mic."""
        while self.active:
            await asyncio.sleep(0.1)
            
            if not self.audio_player.is_playing:
                if self.mic_muted:
                    print("DEBUG: SILENCE DETECTED -> UNMUTING MIC")
                    os.system("amixer sset Capture cap >/dev/null 2>&1; wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0 >/dev/null 2>&1")
                    self.mic_muted = False

    def stop(self):
        self.active = False
        os.system("amixer sset Capture cap >/dev/null 2>&1; wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0 >/dev/null 2>&1")
        
        async def _cleanup():
            if self.room:
                await self.room.disconnect()
            if hasattr(self, 'mic_handle'):
                await self.mic_handle.close()
            self.loop.stop()

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_cleanup(), self.loop)

from PyQt6.QtGui import QRegion, QPainterPath

class MashWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.signals = WorkerSignals()
        self.signals.energy_updated.connect(lambda v: None) # Hidden but signal still exists
        self.signals.status_updated.connect(self.update_status)
        self.signals.expression_updated.connect(self.update_expression)
        self.signals.connected.connect(lambda: self.update_status("connected"))
        self.signals.connection_failed.connect(lambda e: self.update_status("error"))
        
        self.old_pos = QPoint()

        # Start LiveKit
        self.audio_player = AudioPlayer(sample_rate=24000, channels=1)
        self.audio_player.beep(659, 0.1) # Subtle startup blip
        
        self.lk_thread = LiveKitThread(self.signals, self.audio_player)
        self.lk_thread.daemon = True
        self.lk_thread.start()

    def init_ui(self):
        self.setWindowTitle("Mash Portal")
        self.setFixedSize(350, 270) # Massive safety margin
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.central_widget = QFrame()
        self.central_widget.setObjectName("CentralFrame")
        self.set_border_color("#ff3333") 
        self.central_widget.setFixedSize(300, 220)
        
        # Outer container to provide breathing room for the border
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter) # CENTER is key
        self.container_layout.addWidget(self.central_widget)
        
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(10, 10, 10, 10) 
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Video Display (Graphics-based for rounded corners)
        self.scene = QGraphicsScene(self)
        self.video_view = QGraphicsView(self.scene)
        self.video_view.setFixedSize(280, 200) # Smaller to guarantee border visibility
        self.video_view.setStyleSheet("background: transparent; border: none;")
        self.video_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.video_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(QSizeF(280, 200))
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        self.scene.addItem(self.video_item)
        
        layout.addWidget(self.video_view)
        
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setLoops(QMediaPlayer.Loops.Infinite) # Seamless looping
        self.media_player.setVideoOutput(self.video_item)
        
        self.setCentralWidget(self.container)
        
        # Defer initial expression to avoid startup hang
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1000, lambda: self.update_expression("distracted"))

    def apply_mask(self):
        # Apply mask to the video view ONLY, not the whole window
        path = QPainterPath()
        # Radius 25 matches the frame's corner radius
        path.addRoundedRect(0, 0, self.video_view.width(), self.video_view.height(), 25, 25)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.video_view.setMask(region)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-apply mask to the video view on resize
        self.apply_mask()

    def set_border_color(self, color_hex):
        self.central_widget.setStyleSheet(f'''
            #CentralFrame {{
                background-color: rgba(10, 10, 20, 180);
                border-radius: 25px;
                border: 4px solid {color_hex};
            }}
        ''')

    def update_expression(self, name):
        video_path = f"/home/hashir/Documents/mash/videos/{name}.mp4"
        if not os.path.exists(video_path):
            print(f"DEBUG: Video not found: {video_path}, falling back to default")
            video_path = "/home/hashir/Documents/mash/videos/default.mp4"
            
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.media_player.play()

    def update_energy(self, value):
        pass # UI hidden

    def update_status(self, text):
        if "connected" in text.lower():
            self.set_border_color("#00ff99") # Glowing Green
        elif "ready" in text.lower():
            self.set_border_color("#ffff00") # Yellow
        elif "error" in text.lower():
            self.set_border_color("#ff3333") # Red
        else:
            self.set_border_color("#ff9900") # Orange (Connecting)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def closeEvent(self, event):
        self.audio_player.close()
        self.lk_thread.stop()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MashWindow()
    window.show()
    sys.exit(app.exec())
