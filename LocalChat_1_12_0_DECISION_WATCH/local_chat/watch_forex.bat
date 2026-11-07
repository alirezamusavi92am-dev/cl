@echo off
setlocal
cd /d "%~dp0\.."
echo Usage:  watch_forex.bat EURUSD --tf M15            (alert only)
echo         watch_forex.bat EURUSD --tf M15 --mode demo (orders on a DEMO account)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m local_chat.forex_watch %*
    pause
    exit /b
)
python -m local_chat.forex_watch %*
pause
