@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix54 P3-6 QTX Batch Parser
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  echo [Hotfix54] .venv not found. Falling back to py launcher.
  py -3 main.py
)
if errorlevel 1 pause
endlocal
