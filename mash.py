"""
Mash 2.0 - Agentic OS
Main entry point for the Dynamic Island AI Assistant.
"""
import sys
import os
from pathlib import Path

# Ensure project root is on path
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

# Set environment variable for the workspace
os.environ["MASH_WORKSPACE"] = str(project_root)

import logging
from PyQt6.QtWidgets import QApplication
from ui.notch_window import NotchWindow
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("mash")

def main():
    logger.info("Starting Mash 2.0...")
    
    # Force X11 backend to allow absolute window positioning and always-on-top on Wayland
    os.environ["QT_QPA_PLATFORM"] = "xcb"

    # Set up application
    app = QApplication(sys.argv)
    app.setApplicationName("Mash")
    
    # Load environment variables
    load_dotenv()
    
    # Check for API key
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning("OPENROUTER_API_KEY not found in environment. Please add it to your .env file.")

    # Create and show the notch
    notch = NotchWindow()
    notch.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
