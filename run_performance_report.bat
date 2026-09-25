@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Generating Paper Trading Performance Report...
echo ============================================================
if exist "data\oracle_latest.sqlite" (
    python report_paper_performance.py --db data\oracle_latest.sqlite
) else if exist "data\oracle_market_data.sqlite" (
    python report_paper_performance.py --db data\oracle_market_data.sqlite
) else (
    echo Error: No database found at data\oracle_latest.sqlite or data\oracle_market_data.sqlite!
    echo Run pull_oracle_data.bat first to fetch the database from the Oracle VM.
)
echo.
pause
