@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Ask Qt Quick/RHI to prefer the Windows software renderer (WARP) when the
rem hardware graphics driver is unavailable or problematic.
set QSG_RHI_PREFER_SOFTWARE_RENDERER=1
python main.py
if errorlevel 1 pause
