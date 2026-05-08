"""AgenticCodingWorker — multi-step agentic loop for Coding mode."""
import json
import os
import re
import httpx
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from utils.logger import logger


_PLAN_PROMPT = """\
You are an expert software architect. The user wants to build:
"{request}"

Output ONLY a valid JSON array describing the files to create. No explanations, no markdown.
Each item must have:
  "file": "relative/path/to/file.ext"
  "purpose": "one-line description"

Example:
[
  {{"file": "main.py", "purpose": "Entry point"}},
  {{"file": "utils/helpers.py", "purpose": "Utility functions"}}
]
"""

_FILE_PROMPT = """\
You are an expert developer. You are building:
"{request}"

Write the COMPLETE contents of this file: {filename}
Purpose: {purpose}

Output ONLY the raw file content. No markdown fences, no explanation, no comments about what you're doing.
The output will be saved directly to disk.
"""


class AgenticCodingWorker(QThread):
    """Multi-step agentic loop: plan → build each file → report."""

    plan_ready      = pyqtSignal(list)          # list of {"file": ..., "purpose": ...}
    file_started    = pyqtSignal(str)           # filename being generated
    file_done       = pyqtSignal(str, str)      # filename, absolute path
    build_complete  = pyqtSignal(str, list)     # workspace path, list of filenames
    error           = pyqtSignal(str)
    finished        = pyqtSignal()

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, request: str, api_key: str, model_id: str,
                 workspace: str = None, parent=None):
        super().__init__(parent)
        self.request   = request
        self.api_key   = api_key
        self.model_id  = model_id
        self.workspace = workspace or os.path.expanduser(
            f"~/MashWorkspace/session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self._abort = False

    def abort(self):
        self._abort = True

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _call(self, messages: list, max_tokens: int = 4096) -> str:
        """Blocking (non-streaming) API call. Returns full response text."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://mash.local",
            "X-Title": "Mash",
        }
        payload = {
            "model": self.model_id,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(self.API_URL, headers=headers, json=payload)
            logger.debug(f"AgenticWorker API status: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    # ─── Main loop ───────────────────────────────────────────────────────────

    def run(self):
        try:
            os.makedirs(self.workspace, exist_ok=True)

            # ── Step 1: Plan ─────────────────────────────────────────────────
            logger.debug("AgenticWorker: Planning project...")
            plan_text = self._call([
                {"role": "user", "content": _PLAN_PROMPT.format(request=self.request)}
            ], max_tokens=1024)

            # Parse JSON plan (strip any accidental markdown fences)
            plan_text = re.sub(r'^```[^\n]*\n?', '', plan_text.strip())
            plan_text = re.sub(r'```$', '', plan_text.strip())

            try:
                plan = json.loads(plan_text)
                if not isinstance(plan, list):
                    raise ValueError("Not a list")
            except Exception:
                # Fallback: try to extract JSON array from text
                match = re.search(r'\[.*\]', plan_text, re.DOTALL)
                if match:
                    plan = json.loads(match.group())
                else:
                    self.error.emit("Could not parse project plan. Try rephrasing.")
                    self.finished.emit()
                    return

            self.plan_ready.emit(plan)
            logger.debug(f"AgenticWorker: Plan has {len(plan)} files")

            # ── Step 2: Build each file ───────────────────────────────────────
            built_files = []
            for item in plan:
                if self._abort:
                    break

                filename = item.get("file", "unknown.txt").strip()
                purpose  = item.get("purpose", "")

                self.file_started.emit(filename)
                logger.debug(f"AgenticWorker: Generating {filename}")

                content = self._call([
                    {"role": "user", "content": _FILE_PROMPT.format(
                        request=self.request,
                        filename=filename,
                        purpose=purpose
                    )}
                ])

                # Write file
                filepath = os.path.join(self.workspace, filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

                built_files.append(filename)
                self.file_done.emit(filename, filepath)

            self.build_complete.emit(self.workspace, built_files)

        except httpx.HTTPStatusError as e:
            self.error.emit(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            logger.exception("AgenticWorker error")
            self.error.emit(str(e))
        finally:
            self.finished.emit()
