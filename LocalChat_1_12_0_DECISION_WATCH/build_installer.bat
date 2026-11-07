@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [ERROR] Python 3 not found. It is needed ONLY on the build machine, not on the machines that install the app.
  pause & exit /b 1
)

echo [1/4] Running tests...
%PY% -m unittest local_chat.test_server local_chat.test_memory local_chat.test_settings local_chat.test_features local_chat.test_forex
if errorlevel 1 ( echo [ERROR] Tests failed. & pause & exit /b 1 )

echo [2/4] Installing PyInstaller + pywebview (user-level, no virtualenv)...
%PY% -m pip install --user --upgrade pyinstaller pywebview pypdf
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

echo [2b/4] Installing MetaTrader5 package for the forex mode (optional, Windows + 64-bit Python)...
%PY% -m pip install --user --upgrade MetaTrader5
if errorlevel 1 echo [WARN] MetaTrader5 could not be installed: the app will build, but forex mode will say the package is missing.
for /f "tokens=2" %%v in ('%PY% -V') do set "PYVER=%%v"
if "%PYVER%"=="3.10.0" echo [WARN] Python 3.10.0 has a known bug; a workaround is applied in the spec. Upgrading to 3.10.11 or newer is recommended.
echo [3/4] Building LocalChat.exe...
if exist build rmdir /s /q build
%PY% -m PyInstaller packaging\localchat.spec --noconfirm --distpath build\dist --workpath build\work
if errorlevel 1 ( echo [ERROR] PyInstaller failed. & pause & exit /b 1 )

echo [4/4] Building installer...
set "ISCC="
where iscc >nul 2>nul && set "ISCC=iscc"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo [WARN] Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php and run this file again.
  echo        Meanwhile the portable app is ready: build\dist\LocalChat\LocalChat.exe
  pause & exit /b 0
)
"%ISCC%" packaging\installer.iss
if errorlevel 1 ( echo [ERROR] Inno Setup failed. & pause & exit /b 1 )

echo.
echo Done: build\installer\LocalChat-Setup-1.9.0.exe
pause
