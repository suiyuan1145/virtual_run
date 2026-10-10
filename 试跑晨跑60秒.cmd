@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TRIAL_RUNTIME=%~dp0runtime"
if not exist "%TRIAL_RUNTIME%\python.exe" set "TRIAL_RUNTIME=%~dp0dist\虚拟跑步-1.4.3\runtime"
set "TRIAL_DATA=%LOCALAPPDATA%\VirtualRunDesktop"
if defined VIRTUAL_RUN_DATA_DIR set "TRIAL_DATA=%VIRTUAL_RUN_DATA_DIR%"
if not exist "%TRIAL_DATA%\morning\晨跑配置.json" (
  echo 请先在软件“晨跑与登录”页保存自己的起点和晨跑参数。
  pause
  exit /b 1
)
if not exist "%TRIAL_RUNTIME%\python.exe" (
  echo 未找到软件包内的运行环境，请先解压完整软件包。
  pause
  exit /b 1
)
set "PYTHONHOME=%TRIAL_RUNTIME%"
"%TRIAL_RUNTIME%\python.exe" -X utf8 "%~dp0morning_run.py" --config "%TRIAL_DATA%\morning\晨跑配置.json" --trial-seconds 60
pause
