@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Deploying Local Changes to Oracle VM Paper Trading Runner...
echo ============================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "deploy\sync_to_oracle.ps1"
echo.
pause
