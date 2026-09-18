@echo off
rem VOOL bundle launcher: delegate child-process ownership to the bundled Windows supervisor.
setlocal enableextensions
set "VOOL_ROOT=%~dp0"
set "VOOL_BUNDLE_ROOT=%VOOL_ROOT%"
set "VOOL_MANIFEST=%VOOL_ROOT%bundle_manifest.json"
set "VOOL_BUNDLE_MANIFEST=%VOOL_MANIFEST%"
set "VOOL_HOME=%LOCALAPPDATA%\VOOL"
set "OLLAMA_MODELS=%VOOL_HOME%\models"
if not exist "%VOOL_HOME%" mkdir "%VOOL_HOME%" >nul 2>&1
if not exist "%OLLAMA_MODELS%" mkdir "%OLLAMA_MODELS%" >nul 2>&1
if not exist "%VOOL_MANIFEST%" (
  echo ERROR: bundle_manifest.json is missing from this installation.
  exit /b 1
)

rem The supervisor owns bundled ollama.exe, vool_api_server.py --port 11435, and vool_window.py:
rem it uses the
rem manifest-selected model, starts the API before any multi-GB pull completes, reuses healthy
rem external children, records diagnostics, and can be stopped with --stop.
rem Strip the trailing slash for the quoted --root argument; otherwise Windows treats the
rem closing quote as part of the path and the supervisor cannot read bundle_manifest.json.
start "" /B "%VOOL_ROOT%python\pythonw.exe" "%VOOL_ROOT%bundle_supervisor.py" --root "%VOOL_ROOT:~0,-1%"
endlocal
