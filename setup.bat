@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "PYTHON_EXE="
set "FFMPEG_EXE="

call :AddKnownBinDirs

call :EnsurePython
if errorlevel 1 (
    echo.
    echo Failed to prepare Python. Please install Python 3.11+ manually and re-run setup.bat.
    pause
    exit /b 1
)

call :EnsureFfmpeg
if errorlevel 1 (
    echo.
    echo Failed to prepare ffmpeg. Please install FFmpeg manually and re-run setup.bat.
    pause
    exit /b 1
)

if exist "%VENV_DIR%" (
    echo .venv already exists — reusing it.
) else (
    echo Creating virtual environment...
    "%PYTHON_EXE%" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo Upgrading pip inside the project environment...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

echo Installing project dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo Dependency installation failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo Setup complete. Starting the app...
exit /b 0

:AddKnownBinDirs
set "PATH=%PATH%;%ProgramFiles%\ffmpeg\bin;%ProgramFiles(x86)%\ffmpeg\bin;%LOCALAPPDATA%\Microsoft\WinGet\Packages;C:\ffmpeg\bin;C:\Python312\;C:\Python311\;C:\Python310\;"
exit /b 0

:EnsurePython
where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    exit /b 0
)

where winget >nul 2>nul
if not errorlevel 1 (
    echo Python not found on PATH.
    echo Trying to install Python via winget...
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo Python 3.12 install via winget failed. Trying Python 3.11...
        winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    exit /b 0
)

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist "%%~P" (
        set "PYTHON_EXE=%%~P"
        exit /b 0
    )
)

exit /b 1

:EnsureFfmpeg
where ffmpeg >nul 2>nul
if not errorlevel 1 (
    set "FFMPEG_EXE=ffmpeg"
    exit /b 0
)

where winget >nul 2>nul
if not errorlevel 1 (
    echo ffmpeg not found on PATH.
    echo Trying to install FFmpeg via winget...
    winget install --id Gyan.Dev.FFmpeg -e --accept-source-agreements --accept-package-agreements
)

where ffmpeg >nul 2>nul
if not errorlevel 1 (
    set "FFMPEG_EXE=ffmpeg"
    exit /b 0
)

for %%F in (
    "%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"
    "%ProgramFiles(x86)%\ffmpeg\bin\ffmpeg.exe"
    "C:\ffmpeg\bin\ffmpeg.exe"
) do (
    if exist "%%~F" (
        set "FFMPEG_EXE=%%~F"
        set "PATH=%PATH%;%%~dpF"
        exit /b 0
    )
)

exit /b 1
