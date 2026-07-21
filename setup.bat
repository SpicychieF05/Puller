@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found on PATH. Install Python 3.11+ from https://python.org and re-run.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING: ffmpeg not found on PATH.
    echo Video/audio downloads will fail. Install via: winget install ffmpeg
    echo    or visit https://ffmpeg.org/download.html
    echo.
)

if exist .venv (
    echo .venv already exists — skipping creation.
) else (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt

if errorlevel 1 (
    echo Dependency installation failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo Setup complete. Now run run.bat to start the app.
pause
