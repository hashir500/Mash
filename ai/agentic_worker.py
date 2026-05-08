"""AgenticCodingWorker — multi-step agentic loop for Coding mode."""
import json
import os
import re
import httpx
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from utils.logger import logger


_PLAN_PROMPT = """\
You are an expert software architect.
Build plan for: "{request}"

Output ONLY a valid JSON object. Do not include <think> blocks if possible.
No explanations, no markdown fences.

Schema:
{{
  "project_name": "short-slug-name",
  "files": [
    {{"file": "relative/path/file.ext", "purpose": "one-line description"}},
    ...
  ]
}}
"""

_FILE_PROMPT = """\
You are an expert developer.
Building: "{request}"
File: {filename}
Purpose: {purpose}

Write the COMPLETE contents of this file.
Output ONLY the raw file content. No <think> blocks, no markdown fences, no explanation.
"""

_COMMANDS_PROMPT = """\
You just built a project for: "{request}"

Files created: {files}
Workspace: {workspace}

List ONLY the shell commands needed to set up and run this project (e.g., install dependencies, run the app).
Output ONLY a valid JSON array of command strings. No explanations.

Example:
["pip install -r requirements.txt", "python main.py"]
"""


class AgenticCodingWorker(QThread):
    """Multi-step agentic loop: plan → build each file → suggest commands."""

    plan_ready          = pyqtSignal(list, str)     # plan list, project_name
    file_started        = pyqtSignal(str)           # filename being generated
    file_done           = pyqtSignal(str, str)      # filename, absolute path
    build_complete      = pyqtSignal(str, list)     # workspace path, list of filenames
    commands_suggested  = pyqtSignal(list, str)     # list of commands, workspace
    error               = pyqtSignal(str)
    finished            = pyqtSignal()

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, request: str, api_key: str, model_id: str,
                 parent=None):
        super().__init__(parent)
        self.request   = request
        self.api_key   = api_key
        self.model_id  = model_id
        self._abort    = False

    def abort(self):
        self._abort = True

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _call(self, messages: list, max_tokens: int = 4096) -> str:
        """Blocking API call. Returns full response text."""
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
            content = data["choices"][0]["message"]["content"]
            return content if content is not None else ""

    @staticmethod
    def _strip_fences(text: str) -> str:
        # Remove reasoning blocks common in models like DeepSeek/Tencent
        text = re.sub(r'<think>[\s\S]*?</think>', '', text)
        text = text.strip()
        # Remove markdown fences
        text = re.sub(r'^```[^\n]*\n?', '', text)
        text = re.sub(r'```$', '', text)
        return text.strip()

    # ─── Main loop ───────────────────────────────────────────────────────────

    def run(self):
        try:
            # ── Step 1: Plan ─────────────────────────────────────────────────
            logger.debug("AgenticWorker: Planning project...")
            plan_text = self._call([
                {"role": "user", "content": _PLAN_PROMPT.format(request=self.request)}
            ], max_tokens=1024)

            plan_text = self._strip_fences(plan_text)

            # Parse JSON plan
            project_name = "mash_project"
            plan = []
            try:
                obj = json.loads(plan_text)
                if isinstance(obj, dict):
                    raw_name = (obj.get("project_name") or "mash_project").strip().lower()
                    project_name = re.sub(r'[^\w-]', '_', raw_name) or "mash_project"
                    plan = obj.get("files") or []
                elif isinstance(obj, list):
                    plan = obj
            except Exception:
                # Try to locate a JSON object or array anywhere in the response
                # We use a non-greedy search first, then greedy if that fails
                parsed = None
                # Try to find the LARGEST JSON structure in the text
                for pattern in [
                    r'(\{[\s\S]*\})',   # JSON object
                    r'(\[[\s\S]*\])',   # JSON array
                ]:
                    matches = re.findall(pattern, plan_text)
                    if matches:
                        # Try each match from longest to shortest
                        for m in sorted(matches, key=len, reverse=True):
                            try:
                                parsed = json.loads(m)
                                break
                            except Exception:
                                continue
                    if parsed: break

                if parsed is None:
                    logger.error(f"Failed to parse plan. Raw text: {plan_text[:500]}...")
                    self.error.emit("Could not parse project plan. Try rephrasing.")
                    self.finished.emit()
                    return

                if isinstance(parsed, dict):
                    raw_name = (parsed.get("project_name") or "mash_project").strip().lower()
                    project_name = re.sub(r'[^\w-]', '_', raw_name) or "mash_project"
                    plan = parsed.get("files") or []
                elif isinstance(parsed, list):
                    plan = parsed

            # Create workspace with project name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            workspace = os.path.expanduser(
                f"~/MashProjects/{project_name}_{timestamp}"
            )
            os.makedirs(workspace, exist_ok=True)

            self.plan_ready.emit(plan, project_name)
            logger.debug(f"AgenticWorker: Plan has {len(plan)} files → {workspace}")

            # ── Step 2: Build each file ───────────────────────────────────────
            built_files = []
            for item in plan:
                if self._abort:
                    break

                filename = (item.get("file") or "").strip()
                purpose  = (item.get("purpose") or "").strip()

                if not filename:
                    continue

                self.file_started.emit(filename)
                logger.debug(f"AgenticWorker: Generating {filename}")

                content = self._call([
                    {"role": "user", "content": _FILE_PROMPT.format(
                        request=self.request,
                        filename=filename,
                        purpose=purpose
                    )}
                ])

                # Strip accidental markdown fences AND reasoning blocks (<think>)
                content = self._strip_fences(content)

                if not content:
                    logger.warning(f"AgenticWorker: Model returned empty content for {filename}")
                    # Try once more with a more forceful prompt if empty
                    content = self._call([
                        {"role": "user", "content": f"Write only the code for {filename}. No explanations, no preamble. Just the code."},
                        {"role": "assistant", "content": "<think>I need to provide the raw code.</think>"},
                        {"role": "user", "content": f"Building: {self.request}\nFile: {filename}\nPurpose: {purpose}"}
                    ])
                    content = self._strip_fences(content)

                filepath = os.path.join(workspace, filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                
                # Default to a comment if still empty to avoid completely empty files
                if not content:
                    content = f"# Generated file: {filename}\n# Purpose: {purpose}\n# (Model failed to provide content)\n"

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

                built_files.append(filename)
                self.file_done.emit(filename, filepath)

            self.build_complete.emit(workspace, built_files)

            # ── Step 3: Suggest setup commands ───────────────────────────────
            if built_files and not self._abort:
                logger.debug("AgenticWorker: Getting suggested commands...")
                cmd_text = self._call([
                    {"role": "user", "content": _COMMANDS_PROMPT.format(
                        request=self.request,
                        files=", ".join(built_files),
                        workspace=workspace
                    )}
                ], max_tokens=256)

                cmd_text = self._strip_fences(cmd_text)
                try:
                    commands = json.loads(cmd_text)
                    if isinstance(commands, list):
                        # Robustly extract strings, skipping None/non-string items
                        clean = []
                        for c in commands:
                            if c is None:
                                continue
                            if isinstance(c, dict):
                                c = c.get("command") or c.get("cmd") or ""
                            c = str(c).strip()
                            if c:
                                clean.append(c)
                        if clean:
                            self.commands_suggested.emit(clean, workspace)
                except Exception:
                    pass  # silently skip if no commands parsed

        except httpx.HTTPStatusError as e:
            self.error.emit(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            logger.exception("AgenticWorker error")
            self.error.emit(str(e))
        finally:
            self.finished.emit()
