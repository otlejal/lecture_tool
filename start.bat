@echo off
rem Lecture helper launcher: creates a virtual environment on first run,
rem installs requirements and starts the web app. Double-click to run.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.10+ from python.org and tick "Add to PATH".
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

set "PY=.venv\Scripts\python.exe"

echo [INFO] Checking requirements ...
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements. Check the internet connection and the messages above.
    goto :fail
)

echo [INFO] Starting the app. Close this window to stop it.
"%PY%" run.py
if errorlevel 1 goto :fail
goto :end

:fail
echo.
echo Startup failed. See the messages above.
pause
exit /b 1

:end
endlocal
