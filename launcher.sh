#!/bin/bash
# Mash Agent Native Launcher

# Kill any existing Mash processes to prevent ALSA/Microphone conflicts
pkill -f "mash_mono.py" || true
pkill -f "backend_agent/agent.py" || true
pkill -f "frontend_mash/main_ui.py" || true

# Navigate to project directory
cd /home/hashir/Documents/mash

# Source virtual environment
source venv/bin/activate

# Execute Unified Architecture
python3 mash_mono.py >> "/home/hashir/Documents/mash/mash_launcher.log" 2>&1
