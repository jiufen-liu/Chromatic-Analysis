@echo off
setlocal
cd /d "%~dp0"
echo ===============================================
echo Chromatic Analysis Hotfix40 - 3D Window and Visual Polish
echo Folder: %CD%
echo ===============================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import qtx_app.lab3d_quick as m; from qtx_app.build_info import BUILD_ID; print('Quick3D module:',m.__file__); print('Build:',BUILD_ID)"
  ".venv\Scripts\python.exe" main.py
) else (
  echo Local .venv not found. Using python from PATH.
  python -c "import qtx_app.lab3d_quick as m; from qtx_app.build_info import BUILD_ID; print('Quick3D module:',m.__file__); print('Build:',BUILD_ID)"
  python main.py
)
pause
