@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix51 P3-4 Drag Detail Parse Polish
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  echo [Hotfix51] .venv not found. Falling back to py launcher.
  py -3 main.py
)
if errorlevel 1 pause
endlocal
