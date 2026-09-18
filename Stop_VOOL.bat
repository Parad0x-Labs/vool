@echo off
setlocal
REM Emergency stop: kills every VOOL process (API, OpenClaw gateway, agent workers) and disables
REM the auto-restart task, so nothing comes back until you launch VOOL again. Works even if the
REM chat/agent is hung -- you are not relying on the thing you are trying to stop. Ollama (the shared
REM model server) is left running.
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
cd /d "%SCRIPT_ROOT%"

echo Stopping all VOOL processes (API, gateway, agent, auto-restart)...
set "PY=%SCRIPT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m installer.vool_stop --project-root "%SCRIPT_ROOT%"

echo.
echo VOOL is stopped and will not auto-restart. Launch it again from the desktop shortcut when ready.
timeout /t 6 >nul
