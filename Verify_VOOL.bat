@echo off
REM Verify VOOL - double-click to see the signed honesty-receipt proof and check it offline.
REM No accounts, no server. Best run after Install_VOOL (uses the local .venv if present).
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
cd /d "%SCRIPT_ROOT%"
set "PYTHONPATH=%SCRIPT_ROOT%"
set "VENV_PY=%SCRIPT_ROOT%\.venv\Scripts\python.exe"

set "PYEXE="
if exist "%VENV_PY%" set "PYEXE=%VENV_PY%"
if not defined PYEXE (where py >nul 2>&1 && set "PYEXE=py")
if not defined PYEXE (where python >nul 2>&1 && set "PYEXE=python")
if not defined PYEXE (
  echo.
  echo Could not find Python. Install Python 3.10+ from https://python.org,
  echo or run Install_VOOL first, then double-click this again.
  echo.
  pause
  exit /b 1
)

echo Running the VOOL signed honesty-receipt demo...
echo (cryptographic proof of what the agent did - verify it yourself, offline, no server)
echo.
"%PYEXE%" -m core.honesty_receipt demo
echo.
echo ----------------------------------------------------------------
echo To verify your OWN agent's last real session, run:
echo     py -m core.honesty_receipt verify-last
echo ----------------------------------------------------------------
echo.
pause
endlocal
