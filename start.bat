@echo off
chcp 65001 >nul
title Auto Browser
cd /d "%~dp0"

echo ===================================================================
echo     AUTO BROWSER - chay tu ma nguon
echo ===================================================================
echo.
if exist ".git" (
    where git >nul 2>&1 && (
        echo [1/4] Cap nhat ma nguon moi nhat tu GitHub...
        git pull --ff-only
    )
) else (
    echo [1/4] Khong phai ban git clone: bo qua buoc cap nhat.
)

echo [2/4] Kiem tra thu vien Python...
python -m pip install -q -r requirements.txt
python -c "import playwright, google.genai, anthropic, websocket" >nul 2>&1
if errorlevel 1 (
    echo       Cai thu vien bi loi, xem thong bao phia tren.
    pause
    exit /b 1
)
python -m playwright install chromium

echo [3/4] Mo giao dien dieu khien: http://localhost:8000 ...
start "" http://localhost:8000

echo [4/4] Khoi dong may chu (Ctrl+C de dung)...
echo.
python server.py

pause
