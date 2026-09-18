@echo off
setlocal
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m apps.vool_cli stage-trainable-base --activate %*
) else (
  python -m apps.vool_cli stage-trainable-base --activate %*
)
