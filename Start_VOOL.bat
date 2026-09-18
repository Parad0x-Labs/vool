@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
set "PYTHON_EXE=%SCRIPT_ROOT%\.venv\Scripts\python.exe"
set "PYTHONW_EXE=%SCRIPT_ROOT%\.venv\Scripts\pythonw.exe"
set "PYTHONPATH=%SCRIPT_ROOT%"
set "VOOL_PROJECT_ROOT=%SCRIPT_ROOT%"

if not exist "%PYTHON_EXE%" (
  echo VOOL is not installed yet. Bootstrapping...
  call "%SCRIPT_ROOT%\installer\install_vool.bat" /Y "/OPENCLAW=default"
  if errorlevel 1 exit /b 1
)

set "SCRIPT_DRIVE=%~d0"
REM NOTE: `for /f "..." %%A in ('command with its own nested quotes') do ...` silently
REM breaks when the command's own path (here %PYTHON_EXE%) contains a space, which is
REM common on Windows (e.g. this project living under a folder with a space in its name).
REM The nested quoting inside the for/f command clause gets mis-parsed and the loop
REM produces no output, so receipt-derived values silently fall back to hardcoded
REM defaults. Route each python -c call's stdout through a temp file instead, then read
REM that file back with a simple, unnested `for /f ... in ('type "file"') do ...`.
REM Unique per invocation (PID + RANDOM) so overlapping/rapid restarts (e.g. the
REM background watchdog retrying) never collide on the same temp file.
set "RECEIPT_TAG=%RANDOM%_%RANDOM%"
set "RECEIPT_HOME_FILE=%TEMP%\vool_receipt_home_%RECEIPT_TAG%.txt"
set "RECEIPT_PROFILE_FILE=%TEMP%\vool_receipt_profile_%RECEIPT_TAG%.txt"
set "RECEIPT_MODEL_FILE=%TEMP%\vool_receipt_model_%RECEIPT_TAG%.txt"
if "%VOOL_HOME%"=="" (
  "%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('VOOL_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('runtime_home',''))" 1>"%RECEIPT_HOME_FILE%" 2>nul
  set "RECEIPT_HOME="
  for /f "tokens=*" %%A in ('type "%RECEIPT_HOME_FILE%" 2^>nul') do set "RECEIPT_HOME=%%A"
  if not "!RECEIPT_HOME!"=="" set "VOOL_HOME=!RECEIPT_HOME!"
)
if "%VOOL_HOME%"=="" for %%I in ("%SCRIPT_ROOT%\..\.vool_runtime") do set "VOOL_HOME=%%~fI"
set "RECEIPT_INSTALL_PROFILE="
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('VOOL_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print((data.get('install_profile') or {}).get('profile_id',''))" 1>"%RECEIPT_PROFILE_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%RECEIPT_PROFILE_FILE%" 2^>nul') do set "RECEIPT_INSTALL_PROFILE=%%A"
if "%VOOL_INSTALL_PROFILE%"=="" if not "!RECEIPT_INSTALL_PROFILE!"=="" set "VOOL_INSTALL_PROFILE=!RECEIPT_INSTALL_PROFILE!"
if "%VOOL_INSTALL_PROFILE%"=="" set "VOOL_INSTALL_PROFILE=local-only"
set "RECEIPT_MODEL="
"%PYTHON_EXE%" -c "import json, os; from pathlib import Path; p=Path(os.environ.get('VOOL_PROJECT_ROOT',''))/'install_receipt.json'; data=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; print(data.get('selected_model',''))" 1>"%RECEIPT_MODEL_FILE%" 2>nul
for /f "tokens=*" %%A in ('type "%RECEIPT_MODEL_FILE%" 2^>nul') do set "RECEIPT_MODEL=%%A"
if not "!RECEIPT_MODEL!"=="" if not "%VOOL_ALLOW_MODEL_ENV_OVERRIDE%"=="1" set "VOOL_OLLAMA_MODEL=!RECEIPT_MODEL!"
if "%VOOL_OLLAMA_MODEL%"=="" if not "!RECEIPT_MODEL!"=="" set "VOOL_OLLAMA_MODEL=!RECEIPT_MODEL!"
if "%VOOL_OLLAMA_MODEL%"=="" set "VOOL_OLLAMA_MODEL=qwen2.5:7b"
del /f /q "%RECEIPT_HOME_FILE%" "%RECEIPT_PROFILE_FILE%" "%RECEIPT_MODEL_FILE%" >nul 2>&1
if "%OLLAMA_MODELS%"=="" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if not "%OLLAMA_MODELS%"=="" if not exist "%OLLAMA_MODELS%" if exist "%SCRIPT_DRIVE%\Ollama\models" set "OLLAMA_MODELS=%SCRIPT_DRIVE%\Ollama\models"
if "%OLLAMA_API_KEY%"=="" set "OLLAMA_API_KEY=ollama-local"
set "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1"
set "PLAYWRIGHT_ENABLED=1"
set "ALLOW_BROWSER_FALLBACK=1"
set "BROWSER_ENGINE=chromium"
set "WEB_SEARCH_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html"
if "%VOOL_PUBLIC_HIVE_WATCH_HOST%"=="" set "VOOL_PUBLIC_HIVE_WATCH_HOST="
if "%SEARXNG_URL%"=="" set "SEARXNG_URL=http://127.0.0.1:8080"

"%PYTHON_EXE%" -m ops.ensure_public_hive_auth --project-root "%SCRIPT_ROOT%" --watch-host "%VOOL_PUBLIC_HIVE_WATCH_HOST%" >"%TEMP%\vool_public_hive_auth.log" 2>&1
if errorlevel 1 type "%TEMP%\vool_public_hive_auth.log"
where docker >nul 2>&1
if %errorlevel% equ 0 powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_ROOT%\scripts\xsearch_up.ps1" >nul 2>&1

echo Starting VOOL (API + mesh daemon)...
echo OpenClaw connects to http://127.0.0.1:11435
echo.
REM Run the API server windowless. The watchdog launches this detached (no inherited console),
REM so python.exe would ALLOCATE a fresh visible console window in the taskbar. pythonw.exe is the
REM GUI-subsystem interpreter -- it never creates a console -- and the redirect keeps the server
REM output in a log file so nothing is lost. Fall back to python.exe only if pythonw is absent.
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"
REM Single-instance + stale recovery. See docs/audits/vool_api_stale_startup_diagnostic_20260711.md:
REM a stale apps.vool_api_server process that is alive-but-not-listening keeps
REM %TEMP%\vool_api_server.log (and the runtime SQLite DB) locked, so this fresh start's redirect
REM fails with "The process cannot access the file because it is being used by another process" and
REM the launcher retries forever. If the API is already healthy, don't start a duplicate; otherwise
REM terminate any stale server process (it is not serving, since 11435 is unhealthy) to free the locks.
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo VOOL API already healthy on http://127.0.0.1:11435 - not starting a duplicate.
  endlocal
  exit /b 0
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and ($_.CommandLine -like '*apps.vool_api_server*' -or $_.CommandLine -like '*vool_api_server.py*') } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }" >nul 2>&1
powershell -NoProfile -Command "Start-Sleep -Milliseconds 800" >nul 2>&1
"%PYTHONW_EXE%" -m apps.vool_api_server >> "%TEMP%\vool_api_server.log" 2>&1
endlocal
