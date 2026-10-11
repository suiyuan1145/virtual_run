@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "dist\虚拟跑步-1.4.4\启动虚拟跑步.cmd" (
    call "dist\虚拟跑步-1.4.4\启动虚拟跑步.cmd"
    exit /b
)
python -X utf8 android_location_test.py
if errorlevel 1 pause
