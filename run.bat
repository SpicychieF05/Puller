@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

call :EnsureReady
if errorlevel 1 (
    echo.
    echo Could not prepare the project environment.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING: ffmpeg still is not on PATH. Downloads may fail until it is installed.
    echo If needed, run setup.bat again after installing FFmpeg.
    echo.
)

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8000"

echo Starting the app at http://127.0.0.1:8000 (Ctrl+C to stop)
call .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
exit /b 0

:EnsureReady
if not exist ".venv\Scripts\python.exe" (
    echo No project virtual environment found.
    echo Running setup.bat to create it and install dependencies...
    call setup.bat
    if errorlevel 1 exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ffmpeg not found. Installing it via setup.
    call setup.bat
    if errorlevel 1 exit /b 1
)

exit /b 0
