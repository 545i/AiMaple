@echo off
REM Force-stop all maple processes (admin). Double-click to free COM3 / regain mouse.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\kill-admin.ps1"
echo maple stopped (COM3 freed). You can close this window.
timeout /t 3 >nul
