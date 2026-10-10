@echo off
setlocal
set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
set "ANDROID_SDK_ROOT=%ANDROID_HOME%"
set "QT_OPENGL=desktop"
if not exist "%ANDROID_HOME%\emulator\emulator.exe" (
  echo Android Emulator is not installed.
  pause
  exit /b 1
)
"%ANDROID_HOME%\emulator\emulator.exe" -avd VirtualRun_API30 -no-snapshot-load -gpu swangle -feature -Vulkan
if errorlevel 1 pause
