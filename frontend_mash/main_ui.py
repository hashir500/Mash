import sys
import os
import json
import math
import queue
import struct
import asyncio
import threading
import subprocess
from dotenv import load_dotenv


from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QProgressBar, QFrame, QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QPoint, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

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
        self._q = queue.Queue(maxsize=500)
        self._running = False
        self._proc = None
        self._start(sample_rate, channels)

    def _start(self, sample_rate: int, channels: int):
        cmd = [
            'aplay',
            '-f', 'S16_LE',
            '-r', str(sample_rate),
            '-c', str(channels),
            '--buffer-size=24000', # Reduced for lower latency (0.5s)
            '--avail-min=2400',
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
        while self._running and self._proc and self._proc.poll() is None:
            try:
                data = self._q.get(timeout=0.5)  # Unblock every 0.5s to check running
            except queue.Empty:
                continue
            try:
                self._proc.stdin.write(data)
                # No flush() needed — bufsize=0 means stdin is unbuffered
            except (BrokenPipeError, OSError):
                break

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
            self.token = api.AccessToken(api_key, api_secret) \
                .with_identity("mash-frontend") \
                .with_name("Mash UI") \
                .with_grants(api.VideoGrants(room_join=True, can_publish=True, can_publish_data=True, room="mash-room")) \
                .to_jwt()
        else:
            self.token = ""
        
        self.active = True
        self.loop = None

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
            # Microphone
            try:
                self.devices = rtc.MediaDevices()
                # Keep handle reference to prevent garbage collection
                self.mic_handle = self.devices.open_input()
                self._mic_track = rtc.LocalAudioTrack.create_audio_track("microphone", self.mic_handle.source)
                
                # CRITICAL: Must set source=SOURCE_MICROPHONE (2) so that LiveKit's
                # RoomIO on the backend recognizes this as a microphone track.
                # Default is SOURCE_UNKNOWN (0) which RoomIO ignores entirely.
                publish_opts = rtc.TrackPublishOptions()
                publish_opts.source = rtc.TrackSource.SOURCE_MICROPHONE
                await self.room.local_participant.publish_track(self._mic_track, publish_opts)
                print("Microphone activated and publishing as SOURCE_MICROPHONE.")
            except Exception as mic_e:
                print(f"Mic failed: {mic_e}")

                
            self.signals.connected.emit()
        except Exception as e:
            self.signals.connection_failed.emit(str(e))

    async def _stream_to_device(self):
        print("DEBUG: Listening to agent...")
        # Gemini uses 24kHz Mono Int16
        try:
            stream = rtc.AudioStream.from_track(track=self._current_track, sample_rate=24000)
            async for event in stream:
                if not self.active: break
                if event.frame:
                    # Write directly to aplay stdin from this background thread.
                    # Bypasses the Qt signal queue entirely — no frame delays, no drops.
                    self.audio_player.write(bytes(event.frame.data))
        except Exception as e:
            print(f"Stream error: {e}")

    def stop(self):
        self.active = False
        
        async def _cleanup():
            if self.room:
                await self.room.disconnect()
            if hasattr(self, 'mic_handle'):
                await self.mic_handle.close()
            self.loop.stop()

        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_cleanup(), self.loop)

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

        # Audio output: use aplay subprocess instead of Qt multimedia.
        # Qt's QAudioSink drops frames silently when its buffer fills.
        # aplay reads PCM from stdin at playback speed — never drops frames.
        self.audio_player = AudioPlayer(sample_rate=24000, channels=1)
        self.audio_player.beep()
        print("DEBUG: Diagnostic beep sent.")

        # Start LiveKit — pass audio_player directly so the async thread
        # writes audio without going through the Qt signal/event queue.
        self.lk_thread = LiveKitThread(self.signals, self.audio_player)
        self.lk_thread.daemon = True
        self.lk_thread.start()

    def init_ui(self):
        self.setWindowTitle("Mash Portal")
        self.setFixedSize(300, 220)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.central_widget = QFrame()
        self.central_widget.setObjectName("CentralFrame")
        self.set_border_color("#ff3333") # Default Red (Connecting)
        
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(5, 5, 5, 5) # Small padding for border visibility
        
        # Video Display (Eyes/Face)
        self.video_widget = QVideoWidget()
        self.video_widget.setFixedSize(280, 200)
        self.video_widget.setStyleSheet("border-radius: 20px; background-color: black;")
        layout.addWidget(self.video_widget, alignment=Qt.AlignmentFlag.AlignCenter)
        
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)
        # Suppress media player audio (we handle it via aplay)
        self.audio_output = QAudioOutput()
        self.audio_output.setMuted(True)
        self.media_player.setAudioOutput(self.audio_output)
        
        self.media_player.playbackStateChanged.connect(self.handle_playback_state)

        self.setCentralWidget(self.central_widget)
        
        # Initial expression
        self.update_expression("distracted")

    def handle_playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.media_player.play() # Loop

    def set_border_color(self, color_hex):
        self.central_widget.setStyleSheet(f'''
            #CentralFrame {{
                background-color: rgba(10, 10, 20, 200);
                border-radius: 25px;
                border: 3px solid {color_hex};
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
