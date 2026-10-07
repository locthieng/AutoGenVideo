@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
echo.
echo Da cai Gemini web automation. Mo run.bat va bam "Mo Gemini de dang nhap".
echo Tool dung Edge/Chrome da cai, khong can tai Chromium rieng.
pause
