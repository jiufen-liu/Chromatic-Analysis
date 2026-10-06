@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chromatic Analysis - Diagnostics Center
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  set "PY=py -3"
)

%PY% tools\diagnostics_center.py

echo.
pause
endlocal
