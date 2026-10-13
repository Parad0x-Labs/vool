@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
if "%VOOL_HOME%"=="" if not "%NULLA_HOME%"=="" set "VOOL_HOME=%NULLA_HOME%"
set "VOOL_RECEIPT_HOME_FILE=%TEMP%\vool_watchdog_home_%RANDOM%_%RANDOM%.txt"
set "PYTHONPATH=%SCRIPT_ROOT%"
if "%VOOL_HOME%"=="" (
  "%SCRIPT_ROOT%\.venv\Scripts\python.exe" -c "from core.runtime_paths import discover_installed_runtime_home; print(discover_installed_runtime_home() or '')" >"%VOOL_RECEIPT_HOME_FILE%" 2>nul
  for /f "tokens=*" %%A in ('type "%VOOL_RECEIPT_HOME_FILE%" 2^>nul') do set "VOOL_HOME=%%A"
)
if exist "%VOOL_RECEIPT_HOME_FILE%" del /f /q "%VOOL_RECEIPT_HOME_FILE%" >nul 2>&1
if "%VOOL_HOME%"=="" for %%I in ("%SCRIPT_ROOT%\..\.vool_runtime") do set "VOOL_HOME=%%~fI"
set "VOOL_LOG_DIR=%VOOL_HOME%\logs"
if not exist "%VOOL_LOG_DIR%" mkdir "%VOOL_LOG_DIR%"
if not exist "%VOOL_LOG_DIR%" exit /b 1
set "FAILURES=0"

:run
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  set "FAILURES=0"
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
  goto run
)
powershell -NoProfile -Command "try { $client = [Net.Sockets.TcpClient]::new(); $async = $client.BeginConnect('127.0.0.1', 11435, $null, $null); if ($async.AsyncWaitHandle.WaitOne(1000)) { $client.EndConnect($async); $client.Close(); exit 0 }; $client.Close(); exit 1 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  set "FAILURES=0"
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
  goto run
)
rem A self-update is swapping code; do NOT restart mid-swap. But if the lock is stale
rem (older than 10 min, i.e. the updater died), clear it and restart so we never brick.
if not exist "%SCRIPT_ROOT%\.vool_updating.lock" goto no_update_lock
powershell -NoProfile -Command "$f=Get-Item -LiteralPath '%SCRIPT_ROOT%\.vool_updating.lock' -ErrorAction SilentlyContinue; if ($f -and ((Get-Date) - $f.LastWriteTime).TotalMinutes -gt 10) { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue; exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] Stale self-update lock cleared (^>10min); resuming restart. >> "%VOOL_LOG_DIR%\vool_api.log"
  goto no_update_lock
)
echo [%date% %time%] VOOL self-update in progress; watchdog holding restart. >> "%VOOL_LOG_DIR%\vool_api.log"
set "FAILURES=0"
powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
goto run

:no_update_lock
set /a FAILURES+=1
if %FAILURES% LSS 3 (
  echo [%date% %time%] VOOL API probe failed once; waiting before restart. >> "%VOOL_LOG_DIR%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
  goto run
)
set "FAILURES=0"
goto start_api

:start_api
if not exist "%SCRIPT_ROOT%\.venv\Scripts\python.exe" goto direct_start
"%SCRIPT_ROOT%\.venv\Scripts\python.exe" "%SCRIPT_ROOT%\installer\start_windows_detached.py" --cwd "%SCRIPT_ROOT%" --stdout "%VOOL_LOG_DIR%\vool_api_child.log" --stderr "%VOOL_LOG_DIR%\vool_api_child.err.log" -- "%SCRIPT_ROOT%\Start_VOOL.bat" >> "%VOOL_LOG_DIR%\vool_api.log" 2>> "%VOOL_LOG_DIR%\vool_api.err.log"
if errorlevel 1 (
  echo [%date% %time%] VOOL API detached start failed. Retrying... >> "%VOOL_LOG_DIR%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
  goto run
)
echo [%date% %time%] VOOL API detached start requested. >> "%VOOL_LOG_DIR%\vool_api.log"
goto wait_ready

:direct_start
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%SCRIPT_ROOT%\Start_VOOL.bat'; $q=[char]34; Start-Process -WindowStyle Hidden -FilePath $env:ComSpec -ArgumentList @('/c', ($q + $p + $q))" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] VOOL API hidden direct start failed. Retrying... >> "%VOOL_LOG_DIR%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
  goto run
)
echo [%date% %time%] VOOL API hidden direct start requested. >> "%VOOL_LOG_DIR%\vool_api.log"
goto wait_ready

:wait_ready
set "READY=0"
for /L %%i in (1,1,45) do call :probe_ready
if "%READY%"=="1" goto run
echo [%date% %time%] VOOL API did not become healthy after start. Retrying... >> "%VOOL_LOG_DIR%\vool_api.log"
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
goto run

:probe_ready
if "%READY%"=="1" exit /b 0
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:11435/healthz' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 set "READY=1"
powershell -NoProfile -Command "try { $client = [Net.Sockets.TcpClient]::new(); $async = $client.BeginConnect('127.0.0.1', 11435, $null, $null); if ($async.AsyncWaitHandle.WaitOne(1000)) { $client.EndConnect($async); $client.Close(); exit 0 }; $client.Close(); exit 1 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 set "READY=1"
exit /b 0
