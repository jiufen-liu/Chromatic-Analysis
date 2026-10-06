@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix49 P3-3 Stability
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  echo [Hotfix49] .venv not found. Falling back to py launcher.
  py -3 main.py
)
if errorlevel 1 pause
endlocal
