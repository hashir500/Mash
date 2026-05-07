"""OpenRouter streaming client for Mash."""
import json
import httpx
from PyQt6.QtCore import QThread, pyqtSignal


class StreamWorker(QThread):
    """Background thread that streams tokens from OpenRouter."""
    token_received = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, messages: list, api_key: str, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.api_key = api_key
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://mash.local",
                "X-Title": "Mash",
            }
            payload = {
                "model": self.MODEL,
                "messages": self.messages,
                "stream": True,
            }
            with httpx.Client(timeout=90.0) as client:
                with client.stream("POST", self.API_URL, headers=headers, json=payload) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if self._abort:
                            break
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"]
                            content = delta.get("content") or ""
                            if content:
                                self.token_received.emit(content)
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.HTTPStatusError as e:
            self.error.emit(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()
