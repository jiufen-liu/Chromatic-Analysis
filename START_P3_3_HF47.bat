@echo off
setlocal
cd /d "%~dp0"
title Chromatic Analysis - Hotfix47 Performance P3-3

echo ============================================================
echo Chromatic Analysis Hotfix47 - Performance P3-3 Lazy Spectrum
echo ============================================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "from qtx_app.build_info import BUILD_ID; print('Build:',BUILD_ID)"
  ".venv\Scripts\python.exe" main.py
) else (
  python -c "from qtx_app.build_info import BUILD_ID; print('Build:',BUILD_ID)"
  python main.py
)
endlocal
