@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 正在创建项目独立Python环境...
    python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 安装失败。请确认Python已安装，并把项目解压到较短路径，例如 F:\ChromaticAnalysis_v0.11.1。
    pause
    exit /b 1
)
echo.
echo 依赖安装完成。
pause
