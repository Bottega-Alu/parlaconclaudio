@echo off
rem Dev launcher, no console window (pythonw). Logs: voicebridge.log next to this file.
rem A second launch exits on its own while a bridge is already running (single instance).
rem For a console with live log output run instead:  venv\Scripts\python.exe -m src.voice_bridge
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" -m src.voice_bridge
