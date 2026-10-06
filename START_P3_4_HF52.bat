@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix52 P3-4 Import First-Paint Stabilization
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  echo [Hotfix52] .venv not found. Falling back to py launcher.
  py -3 main.py
)
if errorlevel 1 pause
endlocal
