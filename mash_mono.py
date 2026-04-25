import sys
import os
import json
import asyncio
import threading
import traceback
import struct
import math
import base64
import sounddevice as sd
from queue import Queue, Empty
from dotenv import load_dotenv

from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QFrame, QGraphicsView, QGraphicsScene
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPoint, QUrl, QSizeF
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtGui import QRegion, QPainterPath

from google import genai
from google.genai import types

load_dotenv()

# Audio Configuration
SAMPLE_RATE = 24000
CHANNELS = 1
AUDIO_DTYPE = 'int16'

class WorkerSignals(QObject):
    status_updated = pyqtSignal(str)
    expression_updated = pyqtSignal(str)

class NativeAudioLoop:
    def __init__(self, signals: WorkerSignals):
        self.signals = signals
        self.active = True
        self.client = genai.Client() # Uses GEMINI_API_KEY
        self.model = "gemini-3.1-flash-live-preview" 
        self.session = None
        
        self.out_queue = bytearray()
        self.in_queue = asyncio.Queue()
        
        self.speaker_active = False
        self.stream = None
        
    def _audio_callback(self, indata, outdata, frames, time, status):
        """
        Hardware-synchronized absolute Echo cancellation via Mathematical Zeroing.
        """
        wanted_bytes = frames * 2 # 16-bit
        
        chunk_played = False
        if len(self.out_queue) >= wanted_bytes:
            outdata[:] = self.out_queue[:wanted_bytes]
            del self.out_queue[:wanted_bytes]
            chunk_played = True
            # Print S when we are actively speaking audio
            print("S", end="", flush=True) 
        elif len(self.out_queue) > 0:
            chunk = self.out_queue[:]
            del self.out_queue[:]
            outdata[:] = chunk + b'\x00' * (wanted_bytes - len(chunk))
            chunk_played = True
        else:
            outdata[:] = b'\x00' * wanted_bytes
            
        self.speaker_active = chunk_played

        # Digital Mic Muting inline
        if chunk_played:
            pass # Throw away mic input entirely
        else:
            # We must use call_soon_threadsafe to interact with asyncio Queue from audio thread
            loop = getattr(self, "event_loop", None)
            if loop and loop.is_running():
                data_bytes = bytes(indata)
                if not hasattr(self, '_dot_counter'): self._dot_counter = 0
                self._dot_counter += 1
                if self._dot_counter % 50 == 0:
                    print(".", end="", flush=True) 
                loop.call_soon_threadsafe(self.in_queue.put_nowait, data_bytes)

    async def _send_audio_loop(self):
        """Pump captured microphone frames to Gemini."""
        print("DEBUG: Sender Task Started")
        while self.active:
            data = await self.in_queue.get()
            if self.session:
                # Keep stream continuous with digital silence during speaker activity
                # This prevents ping/session timeouts
                current_data = b'\x00' * len(data) if self.speaker_active else data
                try:
                    await self.session.send_realtime_input(
                        audio=types.Blob(data=current_data, mime_type="audio/pcm;rate=24000")
                    )
                except Exception as e:
                    print(f"Error sending audio: {e}")

    async def _receive_loop(self):
        """Receive Gemini responses."""
        print("DEBUG: Receiver Task Started")
        try:
            async for response in self.session.receive():
                if not self.active: break
                
                # Check server content
                if response.server_content and response.server_content.model_turn:
                    parts = response.server_content.model_turn.parts
                    for part in parts:
                        if part.inline_data and part.inline_data.data:
                            # It is AUDIO!
                            self.out_queue.extend(part.inline_data.data)
                
                # Check for silence and restore yellow
                if not self.speaker_active and not len(self.out_queue):
                    pass # We'll handle restoration in a watcher task
                
                # We can inject tool calls/expressions later if needed
        except Exception as e:
            print(f"Receive loop ended/crashed: {e}")

    async def start(self):
        self.event_loop = asyncio.get_running_loop()
        self.stream = sd.RawStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_DTYPE,
            blocksize=2400, # 100ms chunks to prevent network flooding and ping timeouts
            callback=self._audio_callback
        )
        self.stream.start()
        
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(text="You are Mash, a virtual desktop agent. Keep responses short and conversational. Support English and Urdu perfectly. Be friendly and helpful.")])
        )
        
        while self.active:
            try:
                self.signals.status_updated.emit("connecting")
                async with self.client.aio.live.connect(model=self.model, config=config) as session:
                    print("DEBUG: Gemini WebSocket Connected")
                    self.signals.status_updated.emit("ready")
                    self.session = session
                    
                    # Diagnostic Beep natively in sounddevice
                    print("DEBUG: Queuing diagnostic beep...")
                    for i in range(int(24000 * 0.2)):
                        val = int(8000 * math.sin(2 * math.pi * 880 * i / 24000))
                        self.out_queue.extend(struct.pack('<h', val))

                    
                    sender = asyncio.create_task(self._send_audio_loop())
                    receiver = asyncio.create_task(self._receive_loop())
                    watcher = asyncio.create_task(self._status_watcher())
                    
                    await asyncio.gather(sender, receiver, watcher)
            except Exception as e:
                print(f"Websocket connection error: {e}")
                self.signals.status_updated.emit("error")
                await asyncio.sleep(2) # Reconnect delay

    async def _status_watcher(self):
        """Monitor speaker state and update UI colors."""
        last_state = "ready"
        while self.active:
            if self.speaker_active:
                current_state = "speaking"
            elif len(self.out_queue) > 0:
                current_state = "thinking"
            else:
                current_state = "ready"
            
            if current_state != last_state:
                self.signals.status_updated.emit(current_state)
                # If we just finished speaking, ensure we wait a bit before unmuting digital mic
                if last_state == "speaking" and current_state == "ready":
                    await asyncio.sleep(0.5) 
                last_state = current_state
            
            await asyncio.sleep(0.1)

    def stop(self):
        self.active = False
        if self.stream:
            self.stream.stop()
            self.stream.close()

class MashWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.signals = WorkerSignals()
        self.signals.status_updated.connect(self.update_status)
        self.signals.expression_updated.connect(self.update_expression)
        
        self.old_pos = QPoint()

        self.audio_brain = NativeAudioLoop(self.signals)
        
        # Start brain in separate thread
        self.brain_thread = threading.Thread(target=self._run_async_brain, daemon=True)
        self.brain_thread.start()

    def _run_async_brain(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.audio_brain.start())
        except Exception as e:
            print(f"Brain fatal error: {e}")
        finally:
            loop.close()

    def init_ui(self):
        self.setWindowTitle("Mash Portal")
        self.setFixedSize(350, 270)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.central_widget = QFrame()
        self.central_widget.setObjectName("CentralFrame")
        self.set_border_color("#ff9900") 
        self.central_widget.setFixedSize(300, 220)
        
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.container_layout.addWidget(self.central_widget)
        
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(10, 10, 10, 10) 
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.scene = QGraphicsScene(self)
        self.video_view = QGraphicsView(self.scene)
        self.video_view.setFixedSize(280, 200)
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
        self.media_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.media_player.setVideoOutput(self.video_item)
        
        self.setCentralWidget(self.container)
        
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1000, lambda: self.update_expression("distracted"))

    def apply_mask(self):
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.video_view.width(), self.video_view.height(), 25, 25)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.video_view.setMask(region)

    def resizeEvent(self, event):
        super().resizeEvent(event)
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
            video_path = "/home/hashir/Documents/mash/videos/default.mp4"
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.media_player.play()

    def update_status(self, text):
        if "ready" in text.lower() or "connected" in text.lower():
            self.set_border_color("#ffff00") # Yellow (Listening)
        elif "thinking" in text.lower():
            self.set_border_color("#00ccff") # Blue (Brain)
        elif "speaking" in text.lower():
            self.set_border_color("#00ff99") # Mint/Green (Mouth)
        elif "error" in text.lower():
            self.set_border_color("#ff3333") # Red
        else:
            self.set_border_color("#ff9900") # Orange (Connecting)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self.old_pos.isNull():
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = QPoint()

    def closeEvent(self, event):
        self.audio_brain.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MashWindow()
    window.show()
    sys.exit(app.exec())
