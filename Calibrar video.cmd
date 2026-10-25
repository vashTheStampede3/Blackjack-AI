@echo off
cd /d "%~dp0"
start "Calibrar video" ".venv312\Scripts\pythonw.exe" -B -m tools.calibrate_video %*
