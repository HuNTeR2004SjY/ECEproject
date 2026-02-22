@echo off
echo Starting ECE Agent with Cloudflare Tunnel...
echo.

:: Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo Virtual environment not found. Running with system Python...
)

:: Start Flask App in background
start /B python app.py > flask_log.txt 2>&1

:: Wait for Flask to start
echo Waiting for Flask to start...
timeout /t 5 /nobreak >nul

:: Start Cloudflare Tunnel
echo Starting Cloudflare Tunnel...
echo.
echo ========================================================
echo YOUR PUBLIC URL WILL APPEAR BELOW (look for .trycloudflare.com)
echo ========================================================
echo.
cloudflared.exe tunnel --url http://localhost:5000

:: Cleanup on exit
taskkill /F /IM python.exe
