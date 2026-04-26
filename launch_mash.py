#!/usr/bin/env python3
"""
launch_mash.py  –  Unified Mash launcher
=========================================
Starts both the backend agent and the frontend UI as subprocesses.

  Process tree:
    launch_mash.py
    ├── backend_agent/agent.py  start   (LiveKit worker)
    └── frontend_mash/main_ui.py        (Qt desktop widget)

Rules:
  • Either child exiting causes both to be terminated.
  • SIGINT / SIGTERM are forwarded to children then this process exits.
  • The .env file at project root is parsed and injected into env before spawning.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Project layout ────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).resolve().parent
PYTHON        = sys.executable          # same interpreter that is running us
ENV_FILE      = PROJECT_ROOT / ".env"
AGENT_SCRIPT  = PROJECT_ROOT / "backend_agent" / "agent.py"
UI_SCRIPT     = PROJECT_ROOT / "frontend_mash" / "main_ui.py"


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_env_into(env: dict) -> None:
    """Minimal .env reader – no external deps required."""
    if not ENV_FILE.exists():
        print(f"Warning: {ENV_FILE} not found – using system environment only.")
        return
    with open(ENV_FILE) as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            env.setdefault(key, val)   # env vars already set take priority


def kill_all(procs: list[subprocess.Popen]) -> None:
    """Graceful SIGTERM then hard SIGKILL."""
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except OSError:
            pass
    time.sleep(1.5)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except OSError:
            pass


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    # Build a clean environment with .env vars merged in
    env = os.environ.copy()
    load_env_into(env)

    # Ensure both packages can be imported from the project root
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    # ── Spawn backend agent ───────────────────────────────────────────────────
    backend_cmd = [PYTHON, str(AGENT_SCRIPT)]
    print("🧠  Starting Mash backend agent…")
    backend = subprocess.Popen(
        backend_cmd,
        env=env,
        cwd=str(PROJECT_ROOT),
        # Let logs flow to the terminal
        stdout=None,
        stderr=None,
    )

    # Give the agent a couple of seconds to register with LiveKit before the UI
    # tries to connect to the same room.
    time.sleep(2)

    # ── Spawn frontend UI ─────────────────────────────────────────────────────
    frontend_cmd = [PYTHON, str(UI_SCRIPT)]
    print("🖥   Starting Mash frontend UI…")
    frontend = subprocess.Popen(
        frontend_cmd,
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=None,
        stderr=None,
    )

    procs = [backend, frontend]

    # ── Signal handlers – forward to children then exit ───────────────────────
    def _handle_signal(signum, _frame):
        print(f"\n⏹   Mash shutting down (signal {signum})…")
        kill_all(procs)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Watch loop – exit if either child dies ────────────────────────────────
    print("✅  Mash is running. Close the UI or press Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    name = "backend" if p is backend else "frontend"
                    code = p.returncode
                    print(f"⚠   Mash {name} exited (code {code}) – shutting down…")
                    kill_all(procs)
                    sys.exit(code or 0)
    except KeyboardInterrupt:
        kill_all(procs)
        sys.exit(0)


if __name__ == "__main__":
    main()
