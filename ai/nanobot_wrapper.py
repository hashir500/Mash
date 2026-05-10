import subprocess
import os
import json
from PyQt6.QtCore import QThread, pyqtSignal
from utils.logger import logger

class NanobotWorker(QThread):
    """
    Wrapper for the nanobot-ai CLI tool.
    Handles incremental updates and terminal feedback loops.
    """
    output_received = pyqtSignal(str)
    finished = pyqtSignal(bool, str) # (success, last_output)

    def __init__(self, message, workspace, api_key, model_id=None, parent=None):
        super().__init__(parent)
        self.message = message
        self.workspace = workspace
        self.api_key = api_key
        self.model_id = model_id or "openrouter/tencent/hy3-preview"
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        # Ensure workspace exists
        if not os.path.exists(self.workspace):
            os.makedirs(self.workspace, exist_ok=True)

        # Prepare environment
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = self.api_key
        # Add local bin to path just in case
        env["PATH"] = env.get("PATH", "") + ":" + os.path.expanduser("~/.local/bin")

        # Point to our specific config
        config_path = os.path.join(os.path.dirname(__file__), "nanobot_config.json")

        cmd = [
            "nanobot", "agent",
            "-m", self.message,
            "-w", self.workspace,
            "-c", config_path,
            "--no-markdown" 
        ]

        logger.debug(f"Running Nanobot: {' '.join(cmd)} in {self.workspace}")

        try:
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            full_output = []
            while True:
                if self._abort:
                    process.terminate()
                    self.finished.emit(False, "Aborted by user")
                    return

                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    self.output_received.emit(line)
                    full_output.append(line)
            
            returncode = process.wait()
            success = (returncode == 0)
            self.finished.emit(success, "".join(full_output))

        except Exception as e:
            logger.exception("Nanobot execution failed")
            self.finished.emit(False, str(e))
