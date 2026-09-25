@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Running Oracle VM Status Check...
echo ============================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "deploy\status_oracle.ps1"
echo.
echo ============================================================
echo Status check complete. Press any key to close this window...
pause >nul
