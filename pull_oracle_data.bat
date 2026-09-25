@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Downloading Paper Trading SQLite Database from Oracle VM...
echo ============================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "deploy\pull_oracle_data.ps1" -Output "data\oracle_latest.sqlite"
echo.
echo ============================================================
echo Data pull complete! Saved to data\oracle_latest.sqlite
echo Generating local paper trading performance report...
echo ============================================================
python report_paper_performance.py --db data\oracle_latest.sqlite
echo.
pause
