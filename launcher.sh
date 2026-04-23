#!/bin/bash

# Navigate to project directory
cd /home/hashir/Documents/mash

# Source virtual environment
source venv/bin/activate

# Start backend in background
python3 backend_agent/agent.py dev &
BACKEND_PID=$!

# Start frontend in foreground
python3 frontend_mash/main_ui.py

# Cleanup: Kill backend when UI is closed
kill $BACKEND_PID
