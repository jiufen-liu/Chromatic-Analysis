@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==============================================
echo Chromatic Analysis - Windows EXE Build (R1)
echo ==============================================

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] 未找到项目 .venv。请先在固定构建环境中创建虚拟环境并安装依赖。
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

python VERIFY_RELEASE_R1.py
if errorlevel 1 goto :fail

python VERIFY_HOTFIX64_UI.py
if errorlevel 1 goto :fail

python -m pytest -q
if errorlevel 1 goto :fail

python -m PyInstaller --noconfirm --clean "packaging\ChromaticAnalysis.spec"
if errorlevel 1 goto :fail

echo.
echo [OK] EXE 目录已生成：dist\ChromaticAnalysis\
echo 请不要立即对外发布，先执行 CLEAN_MACHINE_TEST_R1.md 的实机验证。
pause
exit /b 0

:fail
echo.
echo [FAILED] 构建前验证或打包失败。未生成可发布版本。
pause
exit /b 1
