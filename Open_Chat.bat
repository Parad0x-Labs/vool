@echo off
setlocal enabledelayedexpansion
REM Open the VOOL chat page - the product's own chat surface served by the
REM always-on API server at http://127.0.0.1:11435/chat (core/vool_chat_page.py).
REM Same ensure-healthy-then-open contract as Open_Web0.bat; the .null browser
REM stays available separately through Open_Web0.bat.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
set "CHAT_URL=http://127.0.0.1:11435/chat"

REM Is VOOL already healthy? If so, just open the browser.
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto open

echo Starting VOOL...
set "LAUNCH_REQUESTED=0"
schtasks /query /tn "VOOL_Daemon" >nul 2>&1
if !errorlevel! equ 0 (
  schtasks /run /tn "VOOL_Daemon" >nul 2>&1
  if !errorlevel! equ 0 set "LAUNCH_REQUESTED=1"
)
if "!LAUNCH_REQUESTED!"=="0" (
  if exist "%SCRIPT_ROOT%\vool_background.vbs" (
    "%SystemRoot%\System32\wscript.exe" "%SCRIPT_ROOT%\vool_background.vbs"
  ) else (
    echo ERROR: VOOL is not installed yet. Run installer\install_vool.bat first.
    exit /b 1
  )
)

set "READY=0"
for /L %%i in (1,1,120) do (
  if !READY! equ 0 (
    powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
    powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! equ 0 set "READY=1"
  )
)
if !READY! neq 1 (
  echo ERROR: VOOL API did not become healthy on http://127.0.0.1:11435/healthz.
  exit /b 1
)

:open
REM The open goes through the same powershell boundary the health checks already use, so
REM the launcher's whole external-command surface stays one isolable contract (and every
REM invocation is shaped the same on a real machine: Start-Process with a URL opens the
REM default browser exactly like `start ""` did).
powershell -NoProfile -Command "Start-Process '%CHAT_URL%'" >nul 2>&1
echo Chat opened at %CHAT_URL%
endlocal
