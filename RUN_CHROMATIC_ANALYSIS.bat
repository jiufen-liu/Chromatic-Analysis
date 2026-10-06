@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
  goto :end
)

where py >nul 2>nul
if not errorlevel 1 (
  py main.py
  goto :end
)

where python >nul 2>nul
if not errorlevel 1 (
  python main.py
  goto :end
)

echo.
echo [Chromatic Analysis] 未找到 Python 运行环境。
echo 开发版请先运行 INSTALL_DEPENDENCIES.bat，正式发布版将使用独立 EXE。
pause
exit /b 1

:end
if errorlevel 1 pause
endlocal
