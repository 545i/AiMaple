@echo off
REM Force-restart maple with admin (kills stuck processes, loads latest code).
REM UAC disabled -> elevates silently. Double-click this if mouse/server misbehaves.
REM
REM KEEP THIS FILE ASCII-ONLY. cmd reads .bat bytes in the OEM codepage (950 here),
REM and a UTF-8 CJK byte is taken as a DBCS lead byte that can swallow the line
REM ending. Two lines then merge and a REM comment turns into a command:
REM   'xxx' is not recognized as an internal or external command
REM Chinese notes about this script belong in scripts\restart-admin.ps1, not here.
REM
REM CALLING FROM A SHELL: use .\restart-admin.bat or the full path. Some environments
REM set NoDefaultCurrentDirectoryInExePath=1, which stops cmd resolving executables
REM from the current directory, so a bare  cmd /c restart-admin.bat  reports
REM "is not recognized as an internal or external command" even though the file is
REM right there. That is the caller's invocation, not the service failing to start.
REM The start.bat lines in bat_err.log / bat_run.log came from exactly that.
net session >nul 2>&1
if %errorlevel% neq 0 (
  REM Do NOT add -Wait here. Start-Process -Wait waits for the whole process tree,
  REM and this script starts a long-lived server, so the window would hang forever.
  REM -WorkingDirectory is explicit because an elevated process otherwise starts in
  REM C:\WINDOWS\system32.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
REM Send the PowerShell stage to a file instead of this console. If the window ever
REM lands in QuickEdit selection mode, console writes block and the restart would
REM stall halfway through: old server killed, new one never started.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-admin.ps1" > "%~dp0scratch_restart_console.log" 2>&1
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo [restart] FAILED with exit code %RC%. scratch_restart.log follows:
  echo ----------------------------------------
  type "%~dp0scratch_restart.log" 2>nul
  echo ----------------------------------------
  echo Press any key to close.
  pause >nul
  exit /b %RC%
)
echo Done. Server is up on :8000. See scratch_restart.log for details.
timeout /t 3 /nobreak >nul 2>&1
exit /b 0
