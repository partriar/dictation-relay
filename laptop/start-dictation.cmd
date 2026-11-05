@echo off
REM Starts dictation relay in the background (tray icon, no console window).
cd /d "%~dp0"
start "" uv run --gui-script relay.py
