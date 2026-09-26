@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "app\start.py"
) else (
  start "" ".venv\Scripts\python.exe" "app\start.py"
)
exit
