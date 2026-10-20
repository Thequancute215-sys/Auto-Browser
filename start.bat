@echo off
chcp 65001 >nul
title AI Browser Hub
cd /d "%~dp0"

echo ===================================================================
echo     AI BROWSER HUB - AUTO LAUNCHER
echo ===================================================================
echo.
echo [1/3] Kiem tra thu vien Python...
python -c "import playwright, google.genai" >nul 2>&1
if errorlevel 1 (
    echo       Dang cai dat thu vien con thieu...
    python -m pip install -r requirements.txt
    python -m playwright install chromium
)

echo [2/3] Mo giao dien dieu khien: http://localhost:8000 ...
start "" http://localhost:8000

echo [3/3] Khoi dong may chu (Ctrl+C de dung)...
echo.
python server.py

pause
