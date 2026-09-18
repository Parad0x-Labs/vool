@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_ROOT=%%~fI"
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
  echo [%date% %time%] Stale self-update lock cleared (^>10min); resuming restart. >> "%TEMP%\vool_api.log"
  goto no_update_lock
)
echo [%date% %time%] VOOL self-update in progress; watchdog holding restart. >> "%TEMP%\vool_api.log"
set "FAILURES=0"
powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
goto run

:no_update_lock
set /a FAILURES+=1
if %FAILURES% LSS 3 (
  echo [%date% %time%] VOOL API probe failed once; waiting before restart. >> "%TEMP%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>&1
  goto run
)
set "FAILURES=0"
goto start_api

:start_api
if not exist "%SCRIPT_ROOT%\.venv\Scripts\python.exe" goto direct_start
"%SCRIPT_ROOT%\.venv\Scripts\python.exe" "%SCRIPT_ROOT%\installer\start_windows_detached.py" --cwd "%SCRIPT_ROOT%" --stdout "%TEMP%\vool_api_child.log" --stderr "%TEMP%\vool_api_child.err.log" -- "%SCRIPT_ROOT%\Start_VOOL.bat" >> "%TEMP%\vool_api.log" 2>> "%TEMP%\vool_api.err.log"
if errorlevel 1 (
  echo [%date% %time%] VOOL API detached start failed. Retrying... >> "%TEMP%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
  goto run
)
echo [%date% %time%] VOOL API detached start requested. >> "%TEMP%\vool_api.log"
goto wait_ready

:direct_start
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%SCRIPT_ROOT%\Start_VOOL.bat'; $q=[char]34; Start-Process -WindowStyle Hidden -FilePath $env:ComSpec -ArgumentList @('/c', ($q + $p + $q))" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] VOOL API hidden direct start failed. Retrying... >> "%TEMP%\vool_api.log"
  powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
  goto run
)
echo [%date% %time%] VOOL API hidden direct start requested. >> "%TEMP%\vool_api.log"
goto wait_ready

:wait_ready
set "READY=0"
for /L %%i in (1,1,45) do call :probe_ready
if "%READY%"=="1" goto run
echo [%date% %time%] VOOL API did not become healthy after start. Retrying... >> "%TEMP%\vool_api.log"
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
