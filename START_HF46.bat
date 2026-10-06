@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Chromatic Analysis Hotfix46 - 3D Navigator / Surface / Perf
echo Left drag = Orbit   Right drag = Pan   Wheel = Zoom
echo ============================================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "from qtx_app.build_info import BUILD_ID; print('Build:',BUILD_ID)"
  ".venv\Scripts\python.exe" main.py
) else (
  python -c "from qtx_app.build_info import BUILD_ID; print('Build:',BUILD_ID)"
  python main.py
)
if errorlevel 1 pause
endlocal
