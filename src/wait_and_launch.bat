@echo off
setlocal enabledelayedexpansion
set ATTEMPTS=0
set MAXATTEMPTS=30

:loop
curl -s -o nul --max-time 1 http://127.0.0.1:5001
if not errorlevel 1 goto launch

set /a ATTEMPTS+=1
if !ATTEMPTS! GEQ !MAXATTEMPTS! goto giveup

timeout /t 1 /nobreak >nul
goto loop

:launch
start "" "CensorUp-Local-WebApp.lnk"
goto end

:giveup
REM Server never responded within 30s - launch anyway rather than leaving you stuck.
REM It'll likely finish loading a few seconds after this, one refresh will fix it.
start "" "CensorUp-Local-WebApp.lnk"

:end