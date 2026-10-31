@echo off
:: Stops the JORDAN TERMINAL server (the live trading engine stops with it).
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
echo Terminal server stopped.
timeout /t 2 >nul
