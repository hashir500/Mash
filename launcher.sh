#!/bin/bash
# Mash Agent Launcher

# Kill any existing Mash processes to prevent ALSA/Microphone conflicts
pkill -f "backend_agent/agent.py" || true
pkill -f "frontend_mash/main_ui.py" || true

# Navigate to project directory
cd /home/hashir/Documents/mash

# Source virtual environment
source venv/bin/activate

# Start backend in background - Dev Mode
python3 backend_agent/agent.py dev >> "/home/hashir/Documents/mash/mash_launcher.log" 2>&1 &
BACKEND_PID=$!

# Start frontend in foreground
python3 frontend_mash/main_ui.py >> "/home/hashir/Documents/mash/mash_launcher.log" 2>&1

# Cleanup: Kill backend and all child processes when UI is closed
kill $BACKEND_PID 2>/dev/null
pkill -P $$ 2>/dev/null
