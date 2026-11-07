@echo off
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY ( echo [ERROR] Python 3 not found - it is needed only on the build machine. & pause & exit /b 1 )
%PY% -m unittest local_chat.test_server local_chat.test_memory local_chat.test_settings local_chat.test_features local_chat.test_forex
if errorlevel 1 ( echo [ERROR] Tests failed. & pause & exit /b 1 )
%PY% -m pip install --user --upgrade pyinstaller pywebview pypdf
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )
%PY% -m pip install --user --upgrade MetaTrader5
if errorlevel 1 echo [WARN] MetaTrader5 could not be installed: forex mode will say the package is missing.
if exist build rmdir /s /q build
%PY% -m PyInstaller packaging\localchat_onefile.spec --noconfirm --distpath build\single --workpath build\work
if errorlevel 1 ( echo [ERROR] PyInstaller failed. & pause & exit /b 1 )
echo.
echo Done: build\single\LocalChat.exe
pause
