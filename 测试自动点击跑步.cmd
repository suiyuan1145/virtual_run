@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TEST_RUNTIME=%~dp0runtime"
if not exist "%TEST_RUNTIME%\python.exe" set "TEST_RUNTIME=%~dp0dist\虚拟跑步-1.4.3\runtime"
if not exist "%TEST_RUNTIME%\python.exe" (
  echo 未找到完整软件包的运行环境，请先解压完整软件包。
  pause
  exit /b 1
)
set "PYTHONHOME=%TEST_RUNTIME%"
"%TEST_RUNTIME%\python.exe" -X utf8 "%~dp0click_run_test.py" --start-emulator --hold-seconds 20
pause
