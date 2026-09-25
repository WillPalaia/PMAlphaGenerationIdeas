@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "deploy\sync_to_oracle.ps1"
echo.
pause
