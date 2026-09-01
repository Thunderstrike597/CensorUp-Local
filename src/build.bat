@echo off
cd /d "%~dp0"

set "EXPECTED_VENV=%cd%\venv"

REM First run / fresh clone: create the venv if it doesn't exist yet.
if not exist "venv\Scripts\python.exe" (
    echo No virtual environment found - creating one now...
    python -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo.
        echo ERROR: Failed to create the virtual environment.
        echo This can happen with the Microsoft Store version of Python.
        echo Try installing Python from https://www.python.org/downloads/
        echo ^(check "Add python.exe to PATH" during setup^), then run this again.
        echo.
        pause
        exit /b 1
    )
    echo Virtual environment created.
)

REM Activate the virtual environment
call venv\Scripts\activate.bat

REM Detect if the project folder was moved and rebuild venv if needed
if /I not "%VIRTUAL_ENV%"=="%EXPECTED_VENV%" (
    echo Project folder appears to have moved - rebuilding the virtual environment...
    call venv\Scripts\deactivate.bat >nul 2>&1
    rmdir /s /q venv
    python -m venv venv
    call venv\Scripts\activate.bat
)

REM Check if dependencies are installed; if not, install them
python -c "import fasthtml, pystray, PIL, PyInstaller" 2>nul
if errorlevel 1 (
    echo Dependencies missing - installing now, this may take a while...
    pip install -r requirements.txt
    pip install pyinstaller
)

echo.
echo Building CensorUp-Local.exe ...
echo.

REM For debugging a crash with no visible error (like on a clean VM):
REM temporarily change --windowed to --console below, rebuild, and run the
REM .exe from an already-open Command Prompt. That keeps a terminal window
REM attached so any crash/traceback actually prints somewhere instead of
REM vanishing with the hidden window. Switch back to --windowed once fixed.
pyinstaller --onefile --windowed --name CensorUp-Local ^
  --icon "assets\icon.ico" ^
  --collect-all whisper ^
  --collect-all whisper_timestamped ^
  --collect-data whisper ^
  --hidden-import=whisper ^
  --hidden-import=whisper_timestamped ^
  --add-data "defaults.json;." ^
  --add-data "assets;assets" ^
  --add-binary "assets\ffmpeg\ffmpeg.exe;." ^
  --add-binary "assets\ffmpeg\ffprobe.exe;." ^
  --add-binary "C:\Windows\System32\vcruntime140.dll;." ^
  --add-binary "C:\Windows\System32\vcruntime140_1.dll;." ^
  --add-binary "C:\Windows\System32\msvcp140.dll;." ^
  main.py

echo.
if exist "dist\CensorUp-Local.exe" (
    echo Build successful! Executable is in the dist\ folder.
) else (
    echo Build failed. Check the errors above.
)

pause