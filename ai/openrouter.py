"""OpenRouter streaming client for Mash."""
import json
import httpx
import os
import base64
from PyQt6.QtCore import QThread, pyqtSignal


class StreamWorker(QThread):
    """Background thread that streams tokens from OpenRouter."""
    token_received = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    MODEL = "google/gemma-4-26b-a4b-it:free"
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, messages: list, api_key: str, attachment_path: str = "", parent=None):
        super().__init__(parent)
        self.messages = messages
        self.api_key = api_key
        self.attachment_path = attachment_path
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            # Handle attachment
            if self.attachment_path and os.path.exists(self.attachment_path):
                ext = self.attachment_path.lower().split('.')[-1]
                if ext in ['png', 'jpg', 'jpeg']:
                    with open(self.attachment_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
                        # Format last user message as list
                        last_msg = self.messages[-1]["content"]
                        self.messages[-1]["content"] = [
                            {"type": "text", "text": last_msg},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                        ]
                elif ext in ['txt', 'csv', 'md']:
                    with open(self.attachment_path, "r", encoding="utf-8") as f:
                        text = f.read()
                        self.messages[-1]["content"] += f"\n\n[Attached File: {os.path.basename(self.attachment_path)}]\n{text}"
                elif ext == 'pdf':
                    try:
                        import PyPDF2
                        text = ""
                        with open(self.attachment_path, "rb") as f:
                            reader = PyPDF2.PdfReader(f)
                            for page in reader.pages:
                                text += page.extract_text() + "\n"
                        self.messages[-1]["content"] += f"\n\n[Attached PDF: {os.path.basename(self.attachment_path)}]\n{text}"
                    except Exception as e:
                        self.error.emit(f"Failed to read PDF: {str(e)}")
                        self.finished.emit()
                        return

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
