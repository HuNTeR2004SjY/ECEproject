@echo off
echo Starting ECE Agent with LocalTunnel (Custom Subdomain)...
echo.

:: Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo Virtual environment not found. Running with system Python...
)

:: Install localtunnel if needed (requires NPM)
where lt >nul 2>nul
if %errorlevel% neq 0 (
    echo LocalTunnel (lt) not found. Installing globally via npm...
    echo You need NodeJS installed for this.
    call npm install -g localtunnel
)

:: Start Flask App in background
start /B python app.py > flask_log.txt 2>&1

:: Wait for Flask to start
echo Waiting for Flask to start...
timeout /t 5 /nobreak >nul

:: Start LocalTunnel
echo.
echo Starting LocalTunnel...
echo Try to claim subdomain: ece-project
echo.
echo ========================================================
echo YOUR URL SHOULD BE: https://ece-project.loca.lt
echo (If taken, it will be random)
echo ========================================================
echo.
call lt --port 5000 --subdomain ece-project

:: Cleanup on exit
taskkill /F /IM python.exe
