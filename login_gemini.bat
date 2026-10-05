@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   KET NOI GEMINI CLI VOI TAI KHOAN GOOGLE (GOI GEMINI PRO)
echo ============================================================
echo.

where gemini.cmd >nul 2>nul
if errorlevel 1 (
    if not exist "%APPDATA%\npm\gemini.cmd" (
        echo Dang cai Gemini CLI...
        call npm.cmd install -g @google/gemini-cli
        if errorlevel 1 (
            echo Loi: can cai Node.js tu https://nodejs.org truoc.
            pause
            exit /b 1
        )
    )
)

if exist "%USERPROFILE%\.gemini\oauth_creds.json" (
    echo Ban DA dang nhap roi. Neu muon doi tai khoan, go /auth trong cua so Gemini.
    echo.
)

echo HUONG DAN:
echo   1. Cua so Gemini se mo ra. Chon "Sign in with Google" (Login with Google).
echo   2. Trinh duyet mo trang dang nhap - chon tai khoan co goi Gemini Pro.
echo   3. Khi thay Gemini san sang (hien o nhap lieu), go  /quit  roi Enter.
echo   4. Quay lai trang web AI Shorts Maker va tai lai trang (F5).
echo.
pause

set GEMINI_API_KEY=
set GOOGLE_API_KEY=
mkdir "%TEMP%\shorts-gemini-login" 2>nul
cd /d "%TEMP%\shorts-gemini-login"
if exist "%APPDATA%\npm\gemini.cmd" (
    call "%APPDATA%\npm\gemini.cmd"
) else (
    call gemini.cmd
)

echo.
if exist "%USERPROFILE%\.gemini\oauth_creds.json" (
    echo [OK] Da ket noi thanh cong!
) else (
    echo [!] Chua thay thong tin dang nhap. Thu chay lai file nay.
)
pause
