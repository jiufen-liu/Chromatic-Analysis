@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chromatic Analysis - Palette Sort Benchmark R1
chcp 65001 >nul

echo ============================================================
echo Chromatic Analysis - 色卡排序专项性能测试 R1
echo ============================================================
echo.
echo 测试内容：
echo   1. QTX解析
echo   2. 普通排序
echo   3. Munsell精确排序（冷/热缓存）
echo   4. Munsell结果缓存重排
echo   5. 光谱排列
echo   6. 光谱 + 感知排列
echo.

set "QTX=%~1"
if not defined QTX (
  echo 请把你实际用于色卡编排的 QTX 文件拖到这个窗口，然后按 Enter：
  set /p "QTX=> "
)
set "QTX=%QTX:"=%"
if not exist "%QTX%" (
  echo.
  echo [错误] 找不到文件：%QTX%
  pause
  exit /b 2
)

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  set "PY=py -3"
)

echo.
echo 开始测试：%QTX%
echo 测试期间窗口可能停留几十秒，这是在计时，不是卡死。
echo.
%PY% tools\palette_sort_benchmark.py "%QTX%"

echo.
echo ============================================================
echo 测试结束。报告位于 performance_reports 文件夹。
echo 请把最新的 PALETTE_SORT_R1_*.txt 发给 ChatGPT。
echo ============================================================
pause
endlocal
