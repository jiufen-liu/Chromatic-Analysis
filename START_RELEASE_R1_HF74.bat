@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  set "PY=py -3"
)
%PY% main.py
if errorlevel 1 pause
