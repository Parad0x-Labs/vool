@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
  "%SCRIPT_DIR%\.venv\Scripts\python.exe" "%SCRIPT_DIR%\installer\provider_probe.py" %*
  exit /b %errorlevel%
)
py -3 "%SCRIPT_DIR%\installer\provider_probe.py" %*
exit /b %errorlevel%
