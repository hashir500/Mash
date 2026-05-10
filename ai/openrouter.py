"""OpenRouter streaming client for Mash."""
import json
import httpx
import os
import base64
from PyQt6.QtCore import QThread, pyqtSignal
from utils.logger import logger


class StreamWorker(QThread):
    """Background thread that streams tokens from OpenRouter."""
    token_received = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    # Default model if none provided
    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
    MODES = {
        "reasoning": "inclusionai/ring-2.6-1t:free",
        "coding": "inclusionai/ring-2.6-1t:free",
        "general": "nvidia/nemotron-nano-12b-v2-vl:free"
    }

    @classmethod
    def get_model_for_mode(cls, mode: str, api_key: str) -> str:
        return cls.MODES.get(mode, cls.DEFAULT_MODEL)

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    reasoning_received = pyqtSignal(str)

    def __init__(self, messages: list, api_key: str, attachment_path: str = "", model_id: str = None, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.api_key = api_key
        self.attachment_path = attachment_path
        self.model_id = model_id or self.DEFAULT_MODEL
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        logger.debug(f"Starting StreamWorker with model: {self.model_id}")
        try:
            # Handle attachment
            if self.attachment_path and os.path.exists(self.attachment_path):
                ext = self.attachment_path.lower().split('.')[-1]
                if ext in ['png', 'jpg', 'jpeg']:
                    with open(self.attachment_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
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

            self._run_openrouter()

        except httpx.HTTPStatusError as e:
            err_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(err_msg)
            self.error.emit(err_msg)
        except Exception as e:
            logger.exception("Unexpected error in StreamWorker")
            self.error.emit(str(e))
        finally:
            logger.debug("StreamWorker finished")
            self.finished.emit()

    def _run_openrouter(self):
        """Standard OpenRouter streaming logic."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://mash.local",
            "X-Title": "Mash",
        }
        payload = {
            "model": self.model_id,
            "messages": self.messages,
            "stream": True,
        }
        with httpx.Client(timeout=90.0) as client:
            with client.stream("POST", self.API_URL, headers=headers, json=payload) as resp:
                logger.debug(f"OpenRouter Response Status: {resp.status_code}")
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if self._abort: break
                    if not line.startswith("data: "): continue
                    data = line[6:].strip()
                    if data == "[DONE]": break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"]
                        
                        reasoning = delta.get("reasoning")
                        if reasoning: self.reasoning_received.emit(reasoning)
                        
                        content = delta.get("content") or ""
                        if content: self.token_received.emit(content)
                    except: continue
