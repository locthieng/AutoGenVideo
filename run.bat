@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Shorts Maker - Backend Server [KHONG TAT CUA SO NAY]
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Dang tao moi truong Python...
    python -m venv .venv || (echo Can cai Python 3.10+ tu python.org & pause & exit /b 1)
    echo [2/2] Dang cai thu vien...
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
    ".venv\Scripts\pip.exe" install -q -r requirements.txt || (pause & exit /b 1)
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Da tao file .env - hay mo file nay va dien API key.
)

echo.
echo ============================================================
echo   KHOI DONG AI SHORTS MAKER...
echo   LUU Y: GIU CUA SO NAY MO TRONG SUOT QUA TRINH DUNG!
echo ============================================================
echo.

".venv\Scripts\python.exe" -m app.server
pause
