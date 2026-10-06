@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Chromatic Analysis Hotfix45 - Pearl Rendering / Taskbar 3D
 echo ============================================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  python main.py
)
if errorlevel 1 pause
endlocal
