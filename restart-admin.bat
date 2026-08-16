@echo off
REM Force-restart maple with admin (kills stuck processes, loads latest code).
REM UAC disabled -> elevates silently. Double-click this if mouse/server misbehaves.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-admin.ps1"
echo Done. See scratch_restart.log for status.
