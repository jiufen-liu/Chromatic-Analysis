@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Performance P3-6 QTX Parser Baseline
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\performance_baseline.py %*
) else (
  py -3 tools\performance_baseline.py %*
)
echo.
echo Report saved under performance_reports\
pause
endlocal
