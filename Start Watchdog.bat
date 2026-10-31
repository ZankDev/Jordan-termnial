@echo off
:: Starts the watchdog hidden in the background: it messages you on Telegram if the terminal server stops answering.
cd /d "%~dp0"
start "" /b ".venv\Scripts\pythonw.exe" launcher\watchdog.py
echo Watchdog started. Stop it from Task Manager (pythonw.exe) when you do not need it.
timeout /t 3 >nul
