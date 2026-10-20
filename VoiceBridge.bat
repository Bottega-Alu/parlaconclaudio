@echo off
rem Dev launcher, no console window (pythonw). Logs: voicebridge.log next to this file.
rem A second launch exits on its own while a bridge is already running (single instance).
rem The process started here is the supervisor: it runs the bridge as a child and
rem relaunches it after a crash (max 3 in 5 min).
rem For a console with live log output run instead:  venv\Scripts\python.exe -m src.voice_bridge
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" -m src.voice_bridge
