@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "AUTO_YES=0"
set "AUTO_START=0"
if "%VOOL_HEADLESS%"=="1" set "AUTO_YES=1"
if "%VOOL_AUTO_YES%"=="1" set "AUTO_YES=1"
if "%VOOL_AUTO_START%"=="1" set "AUTO_START=1"
set "VOOL_HOME_OVERRIDE=%VOOL_HOME%"
set "INSTALL_PROFILE_OVERRIDE=%VOOL_INSTALL_PROFILE%"
set "AGENT_NAME_OVERRIDE=%VOOL_AGENT_NAME%"
set "OPENCLAW_MODE=default"
set "OPENCLAW_PATH_OVERRIDE="
set "OPENCLAW_AGENT_DEFAULT=%USERPROFILE%\.openclaw\agents\main\agent\vool"
set "DESKTOP_SHORTCUT="
set "RUNTIME_REQUIREMENTS=%PROJECT_ROOT%\requirements-runtime.txt"
set "WHEELHOUSE_DIR=%PROJECT_ROOT%\vendor\wheelhouse"
set "BUNDLED_LIQUEFY_DIR=%PROJECT_ROOT%\vendor\liquefy-openclaw-integration"
set "XSEARCH_URL=http://127.0.0.1:8080"
set "WEB_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html"
set "DEFAULT_BROWSER_ENGINE=chromium"
set "PUBLIC_HIVE_SSH_KEY_PATH=%VOOL_PUBLIC_HIVE_SSH_KEY_PATH%"
set "PUBLIC_HIVE_WATCH_HOST=%VOOL_PUBLIC_HIVE_WATCH_HOST%"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
if "%PUBLIC_HIVE_WATCH_HOST%"=="" set "PUBLIC_HIVE_WATCH_HOST="

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/Y" (
  set "AUTO_YES=1"
  shift
  goto parse_args
)
if /i "%~1"=="/START" (
  set "AUTO_START=1"
  shift
  goto parse_args
)
if /i "%~1"=="/INSTALLPROFILE" (
  shift
  if "%~1"=="" (
    echo ERROR: /INSTALLPROFILE requires a value.
    goto usage
  )
  set "INSTALL_PROFILE_OVERRIDE=%~1"
  shift
  goto parse_args
)
if /i "%~1"=="/NOOPENCLAW" (
  set "OPENCLAW_MODE=skip"
  shift
  goto parse_args
)
if /i "%~1"=="/OPENCLAW" (
  shift
  if "%~1"=="" (
    echo ERROR: /OPENCLAW requires a value.
    goto usage
  )
  set "OPENCLAW_RAW=%~1"
  if /i "%OPENCLAW_RAW%"=="skip" set "OPENCLAW_MODE=skip"
  if /i "%OPENCLAW_RAW%"=="default" set "OPENCLAW_MODE=default"
  if /i "%OPENCLAW_RAW%"=="prompt" set "OPENCLAW_MODE=prompt"
  if /i not "%OPENCLAW_RAW%"=="skip" if /i not "%OPENCLAW_RAW%"=="default" if /i not "%OPENCLAW_RAW%"=="prompt" (
    set "OPENCLAW_MODE=path"
    set "OPENCLAW_PATH_OVERRIDE=%OPENCLAW_RAW%"
  )
  shift
  goto parse_args
)
if /i "%~1"=="/HELP" goto usage
if /i "%~1"=="/?" goto usage
set "ARG=%~1"
if /i "!ARG:~0,11!"=="/NULLAHOME=" (
  set "VOOL_HOME_OVERRIDE=!ARG:~11!"
  shift
  goto parse_args
)
if /i "!ARG:~0,11!"=="/AGENTNAME=" (
  set "AGENT_NAME_OVERRIDE=!ARG:~11!"
  shift
  goto parse_args
)
if /i "!ARG:~0,16!"=="/INSTALLPROFILE=" (
  set "INSTALL_PROFILE_OVERRIDE=!ARG:~16!"
  shift
  goto parse_args
)
if /i "!ARG:~0,10!"=="/OPENCLAW=" (
  set "OPENCLAW_RAW=!ARG:~10!"
  if /i "!OPENCLAW_RAW!"=="skip" set "OPENCLAW_MODE=skip"
  if /i "!OPENCLAW_RAW!"=="default" set "OPENCLAW_MODE=default"
  if /i "!OPENCLAW_RAW!"=="prompt" set "OPENCLAW_MODE=prompt"
  if /i not "!OPENCLAW_RAW!"=="skip" if /i not "!OPENCLAW_RAW!"=="default" if /i not "!OPENCLAW_RAW!"=="prompt" (
    set "OPENCLAW_MODE=path"
    set "OPENCLAW_PATH_OVERRIDE=!OPENCLAW_RAW!"
  )
  shift
  goto parse_args
)
echo ERROR: Unknown option %~1
goto usage

:usage
echo Usage: install_vool.bat [/Y] [/START] [/NOOPENCLAW] [/NULLAHOME=PATH] [/INSTALLPROFILE=ID] [/AGENTNAME=NAME] [/OPENCLAW=skip^|default^|prompt^|PATH]
exit /b 2

:args_done
if /i not "!INSTALL_PROFILE_OVERRIDE!"=="" (
  call :validate_install_profile "!INSTALL_PROFILE_OVERRIDE!"
  if errorlevel 1 exit /b 2
)

echo ===============================================
echo VOOL Installer (Windows)
echo This will set up VOOL in the extracted folder.
echo ===============================================
echo.

REM Guarantee a real Python 3.10+ exists, installing one per-user if the host has none, an
REM old one, or only the Microsoft Store stub. This is what makes the single documented
REM command truly one-click on a bare Windows box. ensure_python.ps1 writes the resolved
REM python.exe path to a temp file; we use that concrete path because a Python it just
REM installed will not be on this cmd session's PATH yet.
set "PYFOUND_FILE=%TEMP%\vool_python_path_%RANDOM%_%RANDOM%.txt"
echo Ensuring Python 3.10+ is available (installing it if needed)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%ensure_python.ps1" -OutFile "%PYFOUND_FILE%"
if !errorlevel! neq 0 (
  echo ERROR: Could not find or automatically install Python 3.10+.
  echo Install it from https://www.python.org/downloads/windows/ and retry.
  del /f /q "%PYFOUND_FILE%" >nul 2>&1
  exit /b 1
)
set "PYTHON_EXE="
for /f "usebackq tokens=*" %%A in ("%PYFOUND_FILE%") do set "PYTHON_EXE=%%A"
del /f /q "%PYFOUND_FILE%" >nul 2>&1
if "!PYTHON_EXE!"=="" (
  echo ERROR: Python was located but its path could not be read.
  exit /b 1
)
set PYTHON_CMD="!PYTHON_EXE!"

for %%I in ("%PROJECT_ROOT%\..\.vool_runtime") do set "VOOL_HOME_DEFAULT=%%~fI"
set "AGENT_NAME_DEFAULT=VOOL"
if not "%VOOL_HOME_OVERRIDE%"=="" (
  set "VOOL_HOME=%VOOL_HOME_OVERRIDE%"
) else if "%AUTO_YES%"=="1" (
  set "VOOL_HOME=%VOOL_HOME_DEFAULT%"
) else (
  set /p "VOOL_HOME=VOOL runtime folder [%VOOL_HOME_DEFAULT%]: "
  if "%VOOL_HOME%"=="" set "VOOL_HOME=%VOOL_HOME_DEFAULT%"
)
if not "%AGENT_NAME_OVERRIDE%"=="" set "AGENT_NAME_DEFAULT=%AGENT_NAME_OVERRIDE%"
if "%AUTO_YES%"=="1" (
  set "AGENT_NAME=%AGENT_NAME_DEFAULT%"
) else (
  set /p "AGENT_NAME=Agent display name [%AGENT_NAME_DEFAULT%]: "
  if "%AGENT_NAME%"=="" set "AGENT_NAME=%AGENT_NAME_DEFAULT%"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Step 1/14: Creating virtual environment...
  %PYTHON_CMD% -m venv "%VENV_DIR%"
) else (
  echo Step 1/14: Virtual environment already exists.
)
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo ERROR: Virtual environment Python was not created.
  exit /b 1
)
"%VENV_DIR%\Scripts\python.exe" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
  echo Step 1b/14: Repairing virtual environment pip...
  "%VENV_DIR%\Scripts\python.exe" -m ensurepip --upgrade
  if errorlevel 1 (
    echo WARNING: ensurepip failed. Recreating virtual environment...
    rmdir /s /q "%VENV_DIR%" >nul 2>&1
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if not exist "%VENV_DIR%\Scripts\python.exe" (
      echo ERROR: Virtual environment repair failed.
      exit /b 1
    )
    "%VENV_DIR%\Scripts\python.exe" -m pip --version >nul 2>&1
    if errorlevel 1 (
      echo ERROR: Virtual environment pip is unavailable after repair.
      exit /b 1
    )
  )
)

echo Step 2/14: Installing dependencies (this can take a while)...
set "REQUIREMENTS_FILE=%PROJECT_ROOT%\requirements.txt"
if exist "%RUNTIME_REQUIREMENTS%" set "REQUIREMENTS_FILE=%RUNTIME_REQUIREMENTS%"
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade "pip<26" "setuptools<82" wheel
if exist "%WHEELHOUSE_DIR%\*" (
  echo Using bundled wheelhouse from %WHEELHOUSE_DIR%...
  "%VENV_DIR%\Scripts\python.exe" -m pip install --no-index --find-links "%WHEELHOUSE_DIR%" -r "%REQUIREMENTS_FILE%"
  if !errorlevel! neq 0 (
    echo WARNING: Bundled wheelhouse install failed. Falling back to online install...
    "%VENV_DIR%\Scripts\python.exe" -m pip install "%PROJECT_ROOT%[runtime,proof]"
  )
) else (
  "%VENV_DIR%\Scripts\python.exe" -m pip install "%PROJECT_ROOT%[runtime,proof]"
)
if exist "%WHEELHOUSE_DIR%\*" (
  "%VENV_DIR%\Scripts\python.exe" -m pip install --no-deps "%PROJECT_ROOT%"
)
if !errorlevel! neq 0 (
  echo ERROR: Core dependency installation failed. Cannot continue.
  exit /b 1
)

echo Step 3/14: Installing Playwright browser runtime...
"%VENV_DIR%\Scripts\python.exe" -m playwright install %DEFAULT_BROWSER_ENGINE% >nul 2>"%TEMP%\vool_playwright_install.log"
if !errorlevel! neq 0 (
  echo WARNING: Playwright browser install failed. Browser rendering may stay unavailable until fixed manually.
) else (
  echo Playwright %DEFAULT_BROWSER_ENGINE% runtime installed.
)

echo Step 4/14: Enabling local XSEARCH ^(SearXNG^)...
where docker >nul 2>&1
if !errorlevel! neq 0 (
  echo WARNING: Docker not found. SearXNG bootstrap skipped.
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\scripts\xsearch_up.ps1" >"%TEMP%\vool_xsearch_install.log" 2>&1
  if !errorlevel! neq 0 (
    echo WARNING: Could not start SearXNG automatically. Docker or docker compose may be unavailable.
  ) else (
    powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri '%XSEARCH_URL%/search?q=vool^&format=json' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! neq 0 (
      echo WARNING: SearXNG bootstrap ran but readiness check failed at %XSEARCH_URL%.
    ) else (
      echo Local XSEARCH online at %XSEARCH_URL%
    )
  )
)

REM Liquefy: clone into OpenClaw folder, patch build-backend, install into VOOL venv.
REM This keeps Liquefy scoped to the OpenClaw workspace (not global).
set "LIQUEFY_DIR="
if exist "%BUNDLED_LIQUEFY_DIR%\pyproject.toml" (
  set "LIQUEFY_DIR=%BUNDLED_LIQUEFY_DIR%"
  echo Using bundled Liquefy payload.
) else if exist "%PROJECT_ROOT%\..\liquefy-openclaw-integration\pyproject.toml" (
  set "LIQUEFY_DIR=%PROJECT_ROOT%\..\liquefy-openclaw-integration"
)
if "%LIQUEFY_DIR%"=="" (
  where git >nul 2>&1
  if !errorlevel! equ 0 (
    set "LIQUEFY_DIR=%PROJECT_ROOT%\..\liquefy-openclaw-integration"
    if not exist "%LIQUEFY_DIR%\pyproject.toml" (
      echo Cloning Liquefy into OpenClaw folder...
      git clone --depth 1 https://github.com/Parad0x-Labs/liquefy-openclaw-integration.git "%LIQUEFY_DIR%" >nul 2>&1
    )
    if not exist "%LIQUEFY_DIR%\pyproject.toml" set "LIQUEFY_DIR="
  ) else (
    echo WARNING: git not found and no bundled Liquefy payload is present. Continuing without Liquefy.
  )
)
if not "%LIQUEFY_DIR%"=="" (
  powershell -NoProfile -Command "(Get-Content '%LIQUEFY_DIR%\pyproject.toml') -replace 'setuptools\.backends\._legacy:_Backend','setuptools.build_meta' | Set-Content '%LIQUEFY_DIR%\pyproject.toml'"
  "%VENV_DIR%\Scripts\python.exe" -m pip install "%LIQUEFY_DIR%" >nul 2>&1
  if !errorlevel! equ 0 (
    echo Liquefy installed into VOOL venv from OpenClaw folder.
  ) else (
    echo WARNING: Liquefy installation failed. Continuing without it.
  )
) else (
  echo WARNING: Could not locate Liquefy. Continuing without it.
)

echo Step 5/14: Initializing runtime...
set "VOOL_HOME=%VOOL_HOME%"
"%VENV_DIR%\Scripts\python.exe" -m storage.migrations
if !errorlevel! neq 0 exit /b 1
echo Step 5b/14: Ensuring public Hive auth/bootstrap...
"%VENV_DIR%\Scripts\python.exe" -m ops.ensure_public_hive_auth --project-root "%PROJECT_ROOT%" --watch-host "%PUBLIC_HIVE_WATCH_HOST%" --json >"%TEMP%\vool_public_hive_auth.json" 2>"%TEMP%\vool_public_hive_auth.err"
if !errorlevel! neq 0 (
  echo WARNING: Public Hive auth/bootstrap is incomplete. Public Hive writes and watcher presence/export will stay offline until auth is configured.
  if exist "%TEMP%\vool_public_hive_auth.json" type "%TEMP%\vool_public_hive_auth.json"
  if exist "%TEMP%\vool_public_hive_auth.err" type "%TEMP%\vool_public_hive_auth.err"
) else (
  for /f "tokens=*" %%A in ('type "%TEMP%\vool_public_hive_auth.json"') do set "PUBLIC_HIVE_AUTH_STATUS=%%A"
  echo Public Hive auth/bootstrap status: %PUBLIC_HIVE_AUTH_STATUS%
)
echo Seeding agent identity...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%seed_identity.py" --agent-name "%AGENT_NAME%" 2>nul > "%TEMP%\vool_agent_name.txt"
if exist "%TEMP%\vool_agent_name.txt" (
  set /p AGENT_NAME=<"%TEMP%\vool_agent_name.txt"
  del /f /q "%TEMP%\vool_agent_name.txt" >nul 2>&1
)

echo Step 5c/14: Creating local agent wallet...
REM Wallet encryption is derived from the node signing key, so this MUST run after
REM identity is seeded above. Only the public key is captured; the private seed stays
REM encrypted at rest and is never printed, logged, or written to the receipt.
set "AGENT_WALLET_PUBKEY="
set "WALLET_PUBKEY_FILE=%TEMP%\vool_agent_wallet_%RANDOM%_%RANDOM%.txt"
"%VENV_DIR%\Scripts\python.exe" -m installer.initialize_agent_wallet "%VOOL_HOME%" 1>"%WALLET_PUBKEY_FILE%" 2>nul
if exist "%WALLET_PUBKEY_FILE%" (
  for /f "tokens=*" %%A in ('type "%WALLET_PUBKEY_FILE%" 2^>nul') do set "AGENT_WALLET_PUBKEY=%%A"
  del /f /q "%WALLET_PUBKEY_FILE%" >nul 2>&1
)
if not "%AGENT_WALLET_PUBKEY%"=="" (
  echo Agent wallet ready: %AGENT_WALLET_PUBKEY%
) else (
  echo WARNING: Agent wallet could not be created now. VOOL will create it on first run.
)

echo Step 6/14: Detecting hardware and recommended model...
echo   ^(on a GPU machine this runs a one-time llama.cpp benchmark to verify acceleration; it may take a minute the first time. Set VOOL_SKIP_GPU_PROBE=1 to skip.^)
set "MODEL_TAG="
"%VENV_DIR%\Scripts\python.exe" -c "from core.install_recommendations import build_install_recommendation_truth; print(build_install_recommendation_truth().primary_local_model)" 2>nul > "%TEMP%\vool_model_tag.txt"
set /p MODEL_TAG=<"%TEMP%\vool_model_tag.txt"
del /f /q "%TEMP%\vool_model_tag.txt" >nul 2>&1
if "%MODEL_TAG%"=="" set "MODEL_TAG=qwen2.5:7b"
set "RECOMMENDED_BUNDLE_MODELS="
"%VENV_DIR%\Scripts\python.exe" -c "from core.install_recommendations import build_install_recommendation_truth; print(','.join(build_install_recommendation_truth().recommended_bundle_models))" 2>nul > "%TEMP%\vool_bundle_models.txt"
if exist "%TEMP%\vool_bundle_models.txt" (
  set /p RECOMMENDED_BUNDLE_MODELS=<"%TEMP%\vool_bundle_models.txt"
  del /f /q "%TEMP%\vool_bundle_models.txt" >nul 2>&1
)
if "%RECOMMENDED_BUNDLE_MODELS%"=="" set "RECOMMENDED_BUNDLE_MODELS=%MODEL_TAG%"
"%VENV_DIR%\Scripts\python.exe" -c "import json; from core.hardware_tier import tier_summary; print(json.dumps(tier_summary(), ensure_ascii=False))" 2>nul > "%TEMP%\vool_hw.txt"
set "HARDWARE_SUMMARY="
set /p HARDWARE_SUMMARY=<"%TEMP%\vool_hw.txt"
del /f /q "%TEMP%\vool_hw.txt" >nul 2>&1
set "INSTALL_PROFILE=local-only"
set "RECOMMENDED_INSTALL_PROFILE=local-only"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(selected_model=r'%MODEL_TAG%', runtime_home=r'%VOOL_HOME%', provider_capability_truth=snapshot.capability_truth).profile_id)" 2>nul > "%TEMP%\vool_install_profile.txt"
if exist "%TEMP%\vool_install_profile.txt" (
  set /p RECOMMENDED_INSTALL_PROFILE=<"%TEMP%\vool_install_profile.txt"
  del /f /q "%TEMP%\vool_install_profile.txt" >nul 2>&1
)
if "%INSTALL_PROFILE_OVERRIDE%"=="" (
  if "%AUTO_YES%"=="1" (
    set "INSTALL_PROFILE_OVERRIDE=auto-recommended"
  ) else (
    set /p "INSTALL_PROFILE_OVERRIDE=Install profile [auto-recommended/local-only/local-max] [auto-recommended]: "
    if "%INSTALL_PROFILE_OVERRIDE%"=="" set "INSTALL_PROFILE_OVERRIDE=auto-recommended"
    call :validate_install_profile "%INSTALL_PROFILE_OVERRIDE%"
    if errorlevel 1 exit /b 2
  )
)
set "VOOL_INSTALL_PROFILE=%INSTALL_PROFILE_OVERRIDE%"
set "INSTALL_PROFILE_SUMMARY=%RECOMMENDED_INSTALL_PROFILE% -> %MODEL_TAG%"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(requested_profile=r'%VOOL_INSTALL_PROFILE%', selected_model=r'%MODEL_TAG%', runtime_home=r'%VOOL_HOME%', provider_capability_truth=snapshot.capability_truth).display_summary())" 2>nul > "%TEMP%\vool_install_profile_summary.txt"
if exist "%TEMP%\vool_install_profile_summary.txt" (
  set /p INSTALL_PROFILE_SUMMARY=<"%TEMP%\vool_install_profile_summary.txt"
  del /f /q "%TEMP%\vool_install_profile_summary.txt" >nul 2>&1
)
set "INSTALL_PROFILE=%RECOMMENDED_INSTALL_PROFILE%"
"%VENV_DIR%\Scripts\python.exe" -c "from core.runtime_backbone import build_provider_registry_snapshot; from core.runtime_install_profiles import build_install_profile_truth; snapshot = build_provider_registry_snapshot(); print(build_install_profile_truth(requested_profile=r'%VOOL_INSTALL_PROFILE%', selected_model=r'%MODEL_TAG%', runtime_home=r'%VOOL_HOME%', provider_capability_truth=snapshot.capability_truth).profile_id)" 2>nul > "%TEMP%\vool_selected_install_profile.txt"
if exist "%TEMP%\vool_selected_install_profile.txt" (
  set /p INSTALL_PROFILE=<"%TEMP%\vool_selected_install_profile.txt"
  del /f /q "%TEMP%\vool_selected_install_profile.txt" >nul 2>&1
)
set "VOOL_INSTALL_PROFILE=%INSTALL_PROFILE%"
echo Detected: %HARDWARE_SUMMARY%
echo Selected model: %MODEL_TAG%
echo Recommended local bundle: %RECOMMENDED_BUNDLE_MODELS%
echo Recommended profile: %RECOMMENDED_INSTALL_PROFILE%
echo Install profile: %INSTALL_PROFILE%
REM The summary contains an arrow like "... -> qwen3:4b"; echoing it raw makes cmd treat
REM the ">" as a redirect and write a stray file named after the model (NTFS ADS on the
REM colon). Strip ">" from the display copy so the echo can't redirect.
set "INSTALL_PROFILE_SUMMARY_DISPLAY=!INSTALL_PROFILE_SUMMARY:>=!"
echo Profile summary: !INSTALL_PROFILE_SUMMARY_DISPLAY!
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%validate_install_profile.py" "%VOOL_HOME%" "%MODEL_TAG%" "%INSTALL_PROFILE%" >"%TEMP%\vool_install_profile_validate.txt" 2>&1
if !errorlevel! neq 0 (
  type "%TEMP%\vool_install_profile_validate.txt"
  exit /b 1
)
if exist "%TEMP%\vool_install_profile_validate.txt" del /f /q "%TEMP%\vool_install_profile_validate.txt" >nul 2>&1

echo Step 7/14: Verifying launchers...
for %%L in ("Start_VOOL.bat" "Talk_To_VOOL.bat" "OpenClaw_VOOL.bat" "Stop_VOOL.bat" "vool_background.vbs" "vool_background.cmd") do (
  if not exist "%PROJECT_ROOT%\%%~L" (
    echo ERROR: Missing Windows launcher %%~L.
    exit /b 1
  )
)

REM create_desktop_shortcut.ps1 resolves the REAL (possibly OneDrive/Dropbox-redirected) Desktop
REM and prints where the shortcut actually landed. Capture that so the log is accurate, instead of
REM assuming %USERPROFILE%\Desktop — which is invisible to users whose Desktop is redirected.
set "DESKTOP_SHORTCUT="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%create_desktop_shortcut.ps1" -TargetPath "%PROJECT_ROOT%\OpenClaw_VOOL.bat" -WorkingDirectory "%PROJECT_ROOT%" -LinkPath "%USERPROFILE%\Desktop\OpenClaw + VOOL.lnk" -IconPath "%SCRIPT_DIR%assets\vool.ico" 2^>nul`) do set "DESKTOP_SHORTCUT=%%D"
if defined DESKTOP_SHORTCUT echo Desktop shortcut created: !DESKTOP_SHORTCUT!
if not defined DESKTOP_SHORTCUT echo WARNING: Could not create Desktop shortcut automatically.

REM Also drop a red "Stop VOOL" shortcut: a chat-independent emergency brake that hard-stops every
REM VOOL process and disables auto-restart. The red icon keeps it from being mistaken for launch.
set "STOP_SHORTCUT="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%create_desktop_shortcut.ps1" -TargetPath "%PROJECT_ROOT%\Stop_VOOL.bat" -WorkingDirectory "%PROJECT_ROOT%" -LinkPath "%USERPROFILE%\Desktop\Stop VOOL.lnk" -IconPath "%SCRIPT_DIR%assets\vool_stop.ico" 2^>nul`) do set "STOP_SHORTCUT=%%D"
if defined STOP_SHORTCUT echo Stop shortcut created: !STOP_SHORTCUT!

set "OPENCLAW_ENABLED=1"
if /i "%OPENCLAW_MODE%"=="skip" set "OPENCLAW_ENABLED=0"
if "%AUTO_YES%"=="1" if /i "%OPENCLAW_MODE%"=="prompt" set "OPENCLAW_ENABLED=0"
if "%OPENCLAW_ENABLED%"=="0" goto skip_openclaw

if /i "%OPENCLAW_MODE%"=="prompt" (
  set /p "CREATE_OPENCLAW=Register VOOL in OpenClaw Agent tab? [Y/n]: "
  if /i "!CREATE_OPENCLAW!"=="n" set "OPENCLAW_ENABLED=0"
  if /i "!CREATE_OPENCLAW!"=="no" set "OPENCLAW_ENABLED=0"
)
if "%OPENCLAW_ENABLED%"=="0" goto skip_openclaw

set "OPENCLAW_AGENT_DIR="
if /i "%OPENCLAW_MODE%"=="path" set "OPENCLAW_AGENT_DIR=%OPENCLAW_PATH_OVERRIDE%"
if not "%OPENCLAW_AGENT_DIR%"=="" (
  mkdir "%OPENCLAW_AGENT_DIR%" >nul 2>&1
  copy /Y "%PROJECT_ROOT%\Start_VOOL.bat" "%OPENCLAW_AGENT_DIR%\Start_VOOL.bat" >nul 2>&1
  copy /Y "%PROJECT_ROOT%\Talk_To_VOOL.bat" "%OPENCLAW_AGENT_DIR%\Talk_To_VOOL.bat" >nul 2>&1
)

echo Step 8/14: Registering VOOL in OpenClaw...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%register_openclaw_agent.py" "%PROJECT_ROOT%" "%VOOL_HOME%" "%MODEL_TAG%" "%AGENT_NAME%"
if !errorlevel! neq 0 (
  echo WARNING: Could not register VOOL in OpenClaw config. You can register manually later.
)
goto done_openclaw

:skip_openclaw
echo Step 8/14: OpenClaw registration skipped.

:done_openclaw
echo Step 9/14: Setting up Ollama (local AI runtime)...

REM Resolve drive letter from installer location for model storage
set "INSTALL_DRIVE=%~d0"
if "%INSTALL_DRIVE%"=="" set "INSTALL_DRIVE=C:"
set "OLLAMA_MODELS_DIR="
"%VENV_DIR%\Scripts\python.exe" -c "from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan; models=[m.strip() for m in r'%RECOMMENDED_BUNDLE_MODELS%'.split(',') if m.strip()]; print(build_model_store_drive_plan(required_models=models, support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,))['recommended_model_store_path'])" 2>nul > "%TEMP%\vool_ollama_models_dir.txt"
if exist "%TEMP%\vool_ollama_models_dir.txt" (
  set /p OLLAMA_MODELS_DIR=<"%TEMP%\vool_ollama_models_dir.txt"
  del /f /q "%TEMP%\vool_ollama_models_dir.txt" >nul 2>&1
)
if "%OLLAMA_MODELS_DIR%"=="" set "OLLAMA_MODELS_DIR=%INSTALL_DRIVE%\Ollama\models"
for %%I in ("%OLLAMA_MODELS_DIR%\..") do set "OLLAMA_INSTALL_DIR=%%~fI"
echo Recommended Ollama model store: %OLLAMA_MODELS_DIR%

REM Heal a stale OLLAMA_LLM_LIBRARY that forces Ollama onto the CPU on a GPU machine. This is NOT set
REM by this installer — it's a leftover from past experiments — and it silently defeats GPU serving
REM (a bigger model would run on the CPU). Only clear it when a usable NVIDIA GPU was actually detected
REM (HARDWARE_SUMMARY from Step 6 carries accelerator: cuda / accelerator_status: usable), so a genuine
REM CPU-only box that intentionally set it is left alone.
set "VOOL_GPU_USABLE="
echo !HARDWARE_SUMMARY! | findstr /C:"cuda" >nul && echo !HARDWARE_SUMMARY! | findstr /C:"usable" >nul && set "VOOL_GPU_USABLE=1"
if defined OLLAMA_LLM_LIBRARY if defined VOOL_GPU_USABLE (
  echo !OLLAMA_LLM_LIBRARY! | findstr /I /C:"cpu" >nul && (
    echo WARNING: OLLAMA_LLM_LIBRARY=!OLLAMA_LLM_LIBRARY! forces Ollama onto the CPU, but a usable NVIDIA GPU was detected -- clearing it so your GPU is used.
    reg delete "HKCU\Environment" /v OLLAMA_LLM_LIBRARY /f >nul 2>&1
    set "OLLAMA_LLM_LIBRARY="
  )
)

REM Set permanent env vars so models never land on C: unexpectedly
echo Setting OLLAMA_MODELS=%OLLAMA_MODELS_DIR% (permanent)...
setx OLLAMA_MODELS "%OLLAMA_MODELS_DIR%" >nul 2>&1
set "OLLAMA_MODELS=%OLLAMA_MODELS_DIR%"
echo Setting OLLAMA_API_KEY=ollama-local (permanent)...
setx OLLAMA_API_KEY "ollama-local" >nul 2>&1
set "OLLAMA_API_KEY=ollama-local"
echo Enabling installed Ollama model routing (permanent)...
setx VOOL_REGISTER_INSTALLED_OLLAMA_MODELS "1" >nul 2>&1
set "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1"
REM Context Capsule 2.0 boosters ON by default — turnkey, no manual setx. Adaptive context sizes the
REM window to this machine (never below the shipped baseline); the retrieval booster uses hybrid
REM recall + redaction. To disable either: setx VOOL_ADAPTIVE_CONTEXT 0  /  VOOL_CONTEXT_CAPSULE_V2 0
echo Enabling Context Capsule 2.0 (adaptive context + hybrid recall)...
setx VOOL_ADAPTIVE_CONTEXT "1" >nul 2>&1
set "VOOL_ADAPTIVE_CONTEXT=1"
setx VOOL_CONTEXT_CAPSULE_V2 "1" >nul 2>&1
set "VOOL_CONTEXT_CAPSULE_V2=1"
REM Hide the OpenClaw gateway daemon window: openclaw registers its Windows task with a hidden .vbs
REM launcher (instead of a console .cmd) when this is set, so no gateway console shows in the taskbar.
echo Hiding the background gateway window...
setx OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER "1" >nul 2>&1
set "OPENCLAW_WINDOWS_TASK_HIDDEN_LAUNCHER=1"
echo Persisting selected model/profile runtime config...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%persist_windows_runtime_config.py" "%VOOL_HOME%" "%INSTALL_PROFILE%" "%MODEL_TAG%" "%RECOMMENDED_BUNDLE_MODELS%" "%OLLAMA_MODELS_DIR%" >nul 2>&1
if !errorlevel! neq 0 echo WARNING: Could not persist Windows runtime profile/env config.

REM Check if Ollama is already installed
set "OLLAMA_EXE="
where ollama >nul 2>&1 && set "OLLAMA_EXE=ollama"
if "%OLLAMA_EXE%"=="" if exist "%OLLAMA_INSTALL_DIR%\ollama.exe" set "OLLAMA_EXE=%OLLAMA_INSTALL_DIR%\ollama.exe"
if "%OLLAMA_EXE%"=="" if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if "%OLLAMA_EXE%"=="" (
  echo Ollama not found. Downloading installer...
  set "OLLAMA_SETUP=%TEMP%\OllamaSetup.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%TEMP%\OllamaSetup.exe' -UseBasicParsing"
  if not exist "%TEMP%\OllamaSetup.exe" (
    echo ERROR: Failed to download Ollama. Check your internet connection.
    echo You can install Ollama manually from https://ollama.com/download
    goto skip_ollama_model
  )
  echo Installing Ollama...
  start /wait "" "%TEMP%\OllamaSetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%TEMP%\ollama_install.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
  REM Find the exe after install
  if exist "%OLLAMA_INSTALL_DIR%\ollama.exe" (
    set "OLLAMA_EXE=%OLLAMA_INSTALL_DIR%\ollama.exe"
  ) else if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  )
  del /f /q "%TEMP%\OllamaSetup.exe" >nul 2>&1
  del /f /q "%TEMP%\ollama_install.log" >nul 2>&1
)

if "%OLLAMA_EXE%"=="" (
  echo WARNING: Ollama installation could not be verified. Model pull skipped.
  goto skip_ollama_model
)

echo Step 10/14: Starting Ollama server...
REM Check if Ollama is already serving
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:11434' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! neq 0 (
  start "" /B "%OLLAMA_EXE%" serve
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
)

if "%OPENCLAW_ENABLED%"=="1" (
  echo Step 11/14: Configuring OpenClaw for VOOL...
  where openclaw >nul 2>&1
  REM Delayed expansion is required inside parenthesized blocks: a percent-expanded
  REM errorlevel here would use the PARSE-time value (from before `where openclaw` ran),
  REM and a stale 0 silently skipped the whole OpenClaw install on a machine that lacked it.
  if !errorlevel! neq 0 (
    echo OpenClaw CLI not found on PATH. Installing OpenClaw...
    where npm >nul 2>&1
    if !errorlevel! neq 0 (
      echo WARNING: npm not found on PATH. Cannot install OpenClaw automatically. VOOL registration will still be written locally.
    ) else (
      REM `ollama launch openclaw --config` cannot run headless (it demands an interactive
      REM terminal for model selection even with --yes --model set), and without --config it
      REM launches an attached interactive TUI that would hang this installer. Installing the
      REM npm package directly is synchronous, headless-safe, and gives the same CLI binary;
      REM register_openclaw_agent.py (below) writes all the VOOL-specific config regardless.
      call npm install -g openclaw >nul 2>&1
      if !errorlevel! neq 0 (
        echo WARNING: OpenClaw auto-install via npm failed. VOOL registration will still be written locally.
      )
    )
  )
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%register_openclaw_agent.py" "%PROJECT_ROOT%" "%VOOL_HOME%" "%MODEL_TAG%" "%AGENT_NAME%" >nul 2>&1
  REM Inject the native Web0 pill into OpenClaw's Control UI (idempotent; re-applied on
  REM every launch by OpenClaw_VOOL.bat so it survives OpenClaw npm upgrades).
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%inject_openclaw_web0_pill.py" >nul 2>&1
  if not errorlevel 1 echo Web0 pill added to OpenClaw UI.
  REM Patch OpenClaw's dashboard reply path to retry a conflicted session commit with
  REM backoff, so overlapping turns stop throwing "reply session initialization conflicted".
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%patch_openclaw_session_retry.py" >nul 2>&1
)

echo Step 12/14: Pulling AI model (this may take a while)...

set "MODELS_TO_PULL=%RECOMMENDED_BUNDLE_MODELS%"
if "!MODELS_TO_PULL!"=="" set "MODELS_TO_PULL=%MODEL_TAG%"
set "MODELS_TO_PULL_LIST=%MODELS_TO_PULL:,= %"
for %%M in (%MODELS_TO_PULL_LIST%) do (
  set "PULL_MODEL=%%~M"
  if not "!PULL_MODEL!"=="" (
    "%OLLAMA_EXE%" list 2>nul | findstr /i /c:"!PULL_MODEL!" >nul 2>&1
    if !errorlevel! neq 0 (
      echo Downloading !PULL_MODEL! to %OLLAMA_MODELS_DIR%...
      "%OLLAMA_EXE%" pull !PULL_MODEL!
      if !errorlevel! neq 0 (
        echo WARNING: Model pull failed. You can run this manually later:
        echo   set OLLAMA_MODELS=%OLLAMA_MODELS_DIR%
        echo   "%OLLAMA_EXE%" pull !PULL_MODEL!
      )
    ) else (
      echo Model !PULL_MODEL! already available.
    )
  )
)

if "%OPENCLAW_ENABLED%"=="1" (
  set "OPENCLAW_MEMORY_MODEL=nomic-embed-text"
  "%OLLAMA_EXE%" list 2>nul | findstr /i "!OPENCLAW_MEMORY_MODEL!" >nul 2>&1
  if !errorlevel! neq 0 (
    echo Downloading OpenClaw memory embedding model !OPENCLAW_MEMORY_MODEL! to %OLLAMA_MODELS_DIR%...
    "%OLLAMA_EXE%" pull !OPENCLAW_MEMORY_MODEL!
    if !errorlevel! neq 0 (
      echo WARNING: OpenClaw memory embedding model pull failed. You can run this manually later:
      echo   set OLLAMA_MODELS=%OLLAMA_MODELS_DIR%
      echo   "%OLLAMA_EXE%" pull !OPENCLAW_MEMORY_MODEL!
    )
  ) else (
    echo OpenClaw memory embedding model !OPENCLAW_MEMORY_MODEL! already available.
  )
)

:skip_ollama_model

echo Step 13/14: Registering VOOL as startup task...
set "VBS_PATH=%PROJECT_ROOT%\vool_background.vbs"
set "BACKGROUND_CMD_PATH=%PROJECT_ROOT%\vool_background.cmd"
if not exist "%VBS_PATH%" (
  echo ERROR: Missing %VBS_PATH%.
  exit /b 1
)
if not exist "%BACKGROUND_CMD_PATH%" (
  echo ERROR: Missing %BACKGROUND_CMD_PATH%.
  exit /b 1
)
REM Register with Task Scheduler (runs at logon, no admin required)
schtasks /create /tn "VOOL_Daemon" /tr "\"%SystemRoot%\System32\wscript.exe\" \"%VBS_PATH%\"" /sc onlogon /rl limited /f >nul 2>&1
if !errorlevel! equ 0 (
  echo VOOL registered as startup task.
) else (
  echo WARNING: Could not register startup task. You can start VOOL manually.
)

echo Step 14/14: Configuring Liquefy...
"%VENV_DIR%\Scripts\python.exe" -c "import json; from pathlib import Path; d=Path.home()/'.liquefy'; d.mkdir(parents=True,exist_ok=True); p=d/'config.json'; c={'enabled':True,'version':'1.1.0','mode':'auto','vault_dir':str(d/'vault'),'profile':'default','policy_mode':'strict','verify_mode':'full','encrypt':False,'leak_scan':True}; p.write_text(json.dumps(c,indent=2),encoding='utf-8'); print('Liquefy config written to '+str(p))" 2>nul
if !errorlevel! neq 0 echo WARNING: Could not configure Liquefy.

echo Writing install receipt...
set "OPENCLAW_CONFIG_PATH_RESOLVED="
set "OPENCLAW_AGENT_DIR_RESOLVED="
if "%OPENCLAW_ENABLED%"=="1" (
  REM `for /f ... in ('command with nested quotes') do ...` silently breaks when the
  REM command's own path (here %VENV_DIR%) contains a space (common on Windows), so
  REM route stdout through a temp file instead of an inline for/f command clause.
  set "OC_PATH_TAG=%RANDOM%_%RANDOM%"
  set "OC_CONFIG_PATH_FILE=%TEMP%\vool_oc_config_path_!OC_PATH_TAG!.txt"
  set "OC_AGENT_DIR_FILE=%TEMP%\vool_oc_agent_dir_!OC_PATH_TAG!.txt"
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%print_openclaw_path.py" config_path 1>"!OC_CONFIG_PATH_FILE!" 2>nul
  for /f "tokens=*" %%A in ('type "!OC_CONFIG_PATH_FILE!" 2^>nul') do set "OPENCLAW_CONFIG_PATH_RESOLVED=%%A"
  "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%print_openclaw_path.py" compat_bridge_dir 1>"!OC_AGENT_DIR_FILE!" 2>nul
  for /f "tokens=*" %%A in ('type "!OC_AGENT_DIR_FILE!" 2^>nul') do set "OPENCLAW_AGENT_DIR_RESOLVED=%%A"
  del /f /q "!OC_CONFIG_PATH_FILE!" "!OC_AGENT_DIR_FILE!" >nul 2>&1
  if not "!OPENCLAW_AGENT_DIR!"=="" set "OPENCLAW_AGENT_DIR_RESOLVED=!OPENCLAW_AGENT_DIR!"
)
if "%OPENCLAW_ENABLED%"=="1" if "!OPENCLAW_CONFIG_PATH_RESOLVED!"=="" set "OPENCLAW_CONFIG_PATH_RESOLVED=%USERPROFILE%\.openclaw\openclaw.json"
if "%OPENCLAW_ENABLED%"=="1" if "!OPENCLAW_AGENT_DIR_RESOLVED!"=="" set "OPENCLAW_AGENT_DIR_RESOLVED=%USERPROFILE%\.openclaw\agents\main\agent\vool"
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%write_install_receipt.py" "%PROJECT_ROOT%" "%VOOL_HOME%" "%MODEL_TAG%" "%OPENCLAW_ENABLED%" "!OPENCLAW_CONFIG_PATH_RESOLVED!" "!OPENCLAW_AGENT_DIR_RESOLVED!" "%OLLAMA_EXE%" "%BACKGROUND_CMD_PATH%" "%AGENT_WALLET_PUBKEY%" >nul 2>&1
if !errorlevel! neq 0 echo WARNING: Could not write install receipt.
echo Running VOOL doctor...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%doctor.py" "%PROJECT_ROOT%" "%VOOL_HOME%" "%MODEL_TAG%" "%OPENCLAW_ENABLED%" "!OPENCLAW_CONFIG_PATH_RESOLVED!" "!OPENCLAW_AGENT_DIR_RESOLVED!" "%OLLAMA_EXE%" "%BACKGROUND_CMD_PATH%" >nul 2>&1
if !errorlevel! neq 0 (
  echo WARNING: Could not generate doctor report.
) else (
  echo Doctor report written to %VOOL_HOME%\reports\install_doctor.json
)

echo.
echo Install complete.
echo.
echo ===============================================
echo VOOL is installed. It IS your OpenClaw now.
echo ===============================================
echo.
echo VOOL starts automatically at login. No manual steps.
echo.
echo Visible agent name: %AGENT_NAME%
echo Selected model: %MODEL_TAG%
echo To open now:  %PROJECT_ROOT%\OpenClaw_VOOL.bat
if defined DESKTOP_SHORTCUT echo Desktop:      %DESKTOP_SHORTCUT%
echo.
echo VOOL is the default agent, memory is automatic,
echo mesh daemon is live, starter credits are seeded, and credits are tracked.
echo Install-profile truth is persisted into launchers and support receipts.
echo Playwright browser rendering is enabled through install launchers.
echo Local SearXNG bootstrap is attempted on install and on launcher start.
echo Decentralized AI. Your machine, your node.

if "%AUTO_START%"=="1" (
  echo Launching VOOL now...
  echo   First launch warms the AI model and registers VOOL with Windows startup/env.
  echo   This can take up to ~2 minutes and the window may look idle -- it is not frozen.
  call "%PROJECT_ROOT%\OpenClaw_VOOL.bat"
)

exit /b 0

:validate_install_profile
if "%~1"=="" exit /b 0
if /i "%~1"=="auto-recommended" exit /b 0
if /i "%~1"=="ollama-only" exit /b 0
if /i "%~1"=="local-only" exit /b 0
if /i "%~1"=="ollama-max" exit /b 0
if /i "%~1"=="local-max" exit /b 0
if /i "%~1"=="ollama+kimi" exit /b 0
if /i "%~1"=="hybrid-kimi" exit /b 0
if /i "%~1"=="ollama+tether" exit /b 0
if /i "%~1"=="hybrid-tether" exit /b 0
if /i "%~1"=="hybrid-fallback" exit /b 0
if /i "%~1"=="full-orchestrated" exit /b 0
echo ERROR: /INSTALLPROFILE must be auto-recommended, local-only, or local-max. Got "%~1".
exit /b 1
