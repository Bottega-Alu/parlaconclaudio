@echo off
rem Dev launcher, no console window for the bridge. Logs: voicebridge.log next to this file.
rem Same launch as VoiceBridge.exe (scripts\vb_launcher.py): the venv's BASE pythonw.exe
rem with __PYVENV_LAUNCHER__, because venv\Scripts\pythonw.exe of a uv venv is a
rem console-subsystem trampoline that opens a console window (src\voice_bridge\gui_python.py).
rem A second launch exits on its own while a bridge is already running (single instance).
rem The process started is the supervisor: it runs the bridge as a child and
rem relaunches it after a crash (max 3 in 5 min).
rem For a console with live log output run instead:  venv\Scripts\python.exe -m src.voice_bridge
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0scripts\vb_launcher.py"
