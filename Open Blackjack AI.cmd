@echo off
cd /d "%~dp0"
if not exist ".venv312\Scripts\pythonw.exe" (
  echo Blackjack AI environment is missing. Install Python 3.12 and uv, then run start.ps1 once.
  pause
  exit /b 1
)
start "Blackjack AI" ".venv312\Scripts\pythonw.exe" -B "scripts\launch_blackjack.py"
