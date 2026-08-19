@echo off
REM ============================================================
REM  Fiona puzzle - OBSERVE MODE (records only; never clicks).
REM
REM  ASCII-ONLY ON PURPOSE. Do not put Chinese (or any UTF-8
REM  non-ASCII) into a .bat comment: cmd reads .bat bytes in the
REM  OEM codepage (950 here) and treats a UTF-8 CJK byte as a DBCS
REM  lead byte, which swallows the line terminator. Two lines then
REM  merge and a comment silently becomes a command.
REM
REM  Unrelated to start.bat / restart-admin.bat. Does not need the
REM  service running: this only reads the screen, never touches the
REM  serial port, so it can run alongside the service.
REM
REM  Data goes to fiona_collect\ ; Ctrl+C to stop, it resumes on
REM  the next run.
REM
REM  Usage:
REM    fiona-watch.bat                  live capture
REM    fiona-watch.bat --video x.mp4    replay a recording offline
REM    fiona-watch.bat --summary        print accumulated accuracy
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo [fiona] venv\Scripts\python.exe not found. Run this from the project root.
  pause
  exit /b 1
)

echo [fiona] Observe mode: just play normally. Rounds are logged automatically.
echo [fiona] Ctrl+C to stop.
echo.
venv\Scripts\python.exe -u tools\fiona_watch.py %*
echo.
echo [fiona] Stopped. For accumulated stats: fiona-watch.bat --summary
pause
