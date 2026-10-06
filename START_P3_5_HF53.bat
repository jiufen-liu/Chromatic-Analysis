@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix53 P3-5 Performance Baseline & Compute Cache
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  echo [Hotfix53] .venv not found. Falling back to py launcher.
  py -3 main.py
)
if errorlevel 1 pause
endlocal
