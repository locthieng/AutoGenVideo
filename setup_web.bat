@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
echo.
echo Da cai browser automation cho Gemini web va ChatGPT web.
echo Mo run.bat, chon dich vu va bam nut dang nhap.
pause
