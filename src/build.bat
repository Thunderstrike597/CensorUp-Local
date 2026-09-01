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
    REM NOTE: installs the CPU-only build of torch. It's a fraction of the size
    REM of the default GPU/CUDA build and is all Whisper needs for CPU transcription.
    REM If you ever want GPU-accelerated transcription instead, remove this line
    REM and let "pip install -r requirements.txt" pull the normal torch package.
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install -r requirements.txt
    pip install pyinstaller
)

echo.
echo Building CensorUp-Local ...
echo.

REM For debugging a crash with no visible error (like on a clean VM):
REM temporarily change --windowed to --console below, rebuild, and run the
REM .exe from an already-open Command Prompt. That keeps a terminal window
REM attached so any crash/traceback actually prints somewhere instead of
REM vanishing with the hidden window. Switch back to --windowed once fixed.
REM
REM NOTE: --onefile was removed. Onefile mode re-extracts the entire bundle
REM (incl. torch/whisper/ffmpeg) to a fresh temp folder on every single launch,
REM which is what was causing the multi-minute startup. Without --onefile,
REM PyInstaller builds a folder (dist\CensorUp-Local\) with everything already
REM unpacked, so launches are fast. See installer.iss to package that folder
REM into a normal Windows installer so users still just get one shortcut.
pyinstaller --windowed --name CensorUp-Local ^
  --icon "assets\icon.ico" ^
  --collect-all whisper ^
  --collect-all whisper_timestamped ^
  --collect-data whisper ^
  --hidden-import=whisper ^
  --hidden-import=whisper_timestamped ^
  --add-data "defaults.json;." ^
  --add-data "assets;assets" ^
  --add-binary "C:\Windows\System32\vcruntime140.dll;." ^
  --add-binary "C:\Windows\System32\vcruntime140_1.dll;." ^
  --add-binary "C:\Windows\System32\msvcp140.dll;." ^
  main.py

echo.
if exist "dist\CensorUp-Local\CensorUp-Local.exe" (
    echo Build successful! Folder is in dist\CensorUp-Local\
    echo Run installer.iss with Inno Setup to package it into a single installer.
) else (
    echo Build failed. Check the errors above.
)

pause
