@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "deploy\pull_oracle_data.ps1" -Output "data\oracle_latest.sqlite"
echo.
pause
