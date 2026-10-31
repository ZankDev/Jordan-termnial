@echo off
:: Template for run.bat (run.bat itself holds your real keys and is never committed).
:: Copy to run.bat and fill in the values.
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="

:: Telegram trade alerts (photo + setup on entry, live PnL edits, close summary).
set "TELEGRAM_BOT_TOKEN="
set "TELEGRAM_CHAT_ID="

:: TradersPost webhook (-> Apex / Tradovate): fires a real order only for the one account you explicitly link on the
:: ACCOUNTS page. Point it at a PAPER account until you are ready to go live.
set "TRADERSPOST_WEBHOOK_URL="

:: AI provider key: news summaries and the "AI <question>" command bar.
set "AI_API_KEY="
set "AI_MODEL="

echo Starting NOSTRO Algo Terminal...
echo Open http://localhost:8000 in your browser once it says "Application startup complete".
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
pause
