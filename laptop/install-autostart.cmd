@echo off
REM One-time: make dictation relay start automatically at login (and start it now).
cd /d "%~dp0"
uv run relay.py --install-startup
pause
