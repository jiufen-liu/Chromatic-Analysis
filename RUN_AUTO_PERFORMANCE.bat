@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chromatic Analysis - One Click Performance
chcp 65001 >nul
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  set "PY=py -3"
)
%PY% tools\auto_performance_suite.py
echo.
pause
endlocal
