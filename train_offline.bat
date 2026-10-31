@echo off
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="

echo Running NOSTRO offline trainer (no server/dashboard needed)...
echo Pass args to customize, e.g.: train_offline.bat --cycles 0
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" scripts\offline_train.py %*
pause
