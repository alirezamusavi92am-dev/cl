@echo off
setlocal
cd /d "%~dp0\.."
echo Usage example:  validate_forex.bat EURUSD GBPUSD XAUUSD --tf M15 H1
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m local_chat.forex_validate %*
    pause
    exit /b
)
python -m local_chat.forex_validate %*
pause
