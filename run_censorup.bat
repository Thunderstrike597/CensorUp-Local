@echo off
REM Move into the "Main" subfolder (where main.py and venv actually live)
cd /d "%~dp0src"

REM First run / fresh clone: create the venv if it doesn't exist yet
if not exist "venv\Scripts\python.exe" (
    echo No virtual environment found — creating one now...
    python -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo.
        echo ERROR: Failed to create the virtual environment.
        echo This can happen with the Microsoft Store version of Python.
        echo Try installing Python from https://python.org instead
        echo ^(check "Add python.exe to PATH" during setup^), then run this again.
        echo.
        pause
        exit /b 1
    )
    echo Virtual environment created.
)

REM Activate the virtual environment
call venv\Scripts\activate.bat

REM Sanity check: make sure "python" now actually resolves inside this venv,
REM not a global/Store install (this is what silently breaks installs)
for /f "delims=" %%P in ('where python') do (
    echo Using Python at: %%P
    echo %%P | find /i "%cd%\venv" >nul
    if errorlevel 1 (
        echo.
        echo WARNING: The active "python" is NOT inside this project's venv.
        echo Dependency installs may end up in the wrong place.
        echo If things fail below, delete the "venv" folder and try again,
        echo or install Python from https://python.org and re-run this script.
        echo.
    )
    goto donecheck
)
:donecheck

REM Check if dependencies are installed; if not, install them
python -c "import fasthtml" 2>nul
if errorlevel 1 (
    echo Dependencies missing - installing now, this may take a while...
    pip install -r requirements.txt
)

REM Poll the server in the background and launch the app the moment it actually
REM responds, instead of guessing a fixed delay
start /b "" cmd /c wait_and_launch.bat

REM Launch the server (stays in THIS window, same as before)
python main.py

REM Keep the window open if it crashes or exits, so you can read the error
pause