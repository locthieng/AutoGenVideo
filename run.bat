@echo off
chcp 65001 >nul
cd /d "%~dp0"
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

".venv\Scripts\python.exe" -m app.server
pause
