#!/usr/bin/env python3
"""
Launcher script for the Microcap Stock Screener & AI Research Tool
Run this from the creativity_UBC folder: python run_microcap_app.py
"""

import subprocess
import sys
import os

def main():
    # Ensure we're in the creativity_UBC folder
    if not os.path.exists('microcap'):
        print("❌ Error: microcap folder not found!")
        print("Please run this script from the creativity_UBC folder.")
        sys.exit(1)
    
    # Check if app.py exists
    app_path = os.path.join('microcap', 'app.py')
    if not os.path.exists(app_path):
        print(f"❌ Error: {app_path} not found!")
        sys.exit(1)
    
    print("🚀 Starting Microcap Stock Screener & AI Research Tool...")
    print(f"📁 Running from: {os.getcwd()}")
    print(f"🔧 App location: {app_path}")
    print("-" * 60)
    
    # Run streamlit with the app
    try:
        subprocess.run([
            sys.executable, "-m", "streamlit", "run", app_path,
            "--server.headless", "false"
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running streamlit: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("❌ Error: Streamlit not found. Install it with: pip install streamlit")
        sys.exit(1)

if __name__ == "__main__":
    main()