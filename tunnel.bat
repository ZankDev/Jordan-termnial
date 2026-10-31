@echo off
cd /d "%~dp0"
setlocal

set "CF=C:\Program Files (x86)\cloudflared\cloudflared.exe"
where cloudflared >nul 2>nul
if %errorlevel%==0 set "CF=cloudflared"

echo ============================================================
echo   JORDAN TERMINAL - Cloudflare Tunnel
echo ============================================================
echo.
echo This opens a public HTTPS link that forwards to your terminal
echo at http://localhost:8000 - make sure run.bat is already
echo running in another window before you continue.
echo.
echo Once connected you'll see a line like:
echo   https://some-random-words.trycloudflare.com
echo That is your public URL - copy it and send it back to Claude.
echo.
echo Before anyone else can use that link, open it yourself once
echo and set an access password at /login - the terminal has no
echo login by default.
echo.
echo Keep this window open. Closing it disconnects the tunnel.
echo ============================================================
echo.

"%CF%" tunnel --url http://localhost:8000

echo.
echo Tunnel closed.
pause
