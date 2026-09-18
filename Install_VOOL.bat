@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%installer\install_vool.bat" %*
endlocal
