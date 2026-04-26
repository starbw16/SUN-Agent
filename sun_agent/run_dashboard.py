#!/usr/bin/env python3
"""
Launch the SUN-Agent web dashboard.
  python3 run_dashboard.py
Then open http://localhost:5050
"""
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

from src.dashboard.app import app

if __name__ == "__main__":
    app.run(debug=True, port=5050, host="127.0.0.1")
