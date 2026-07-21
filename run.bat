@echo off
cd /d "%~dp0"

if not exist .venv (
    echo No .venv\ found — run setup.bat first.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING: ffmpeg not found on PATH. Downloads will fail without it.
)

call .venv\Scripts\activate.bat

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8000"

echo Starting the app at http://127.0.0.1:8000  (Ctrl+C to stop)
uvicorn main:app --host 127.0.0.1 --port 8000
