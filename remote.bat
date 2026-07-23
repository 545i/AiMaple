@echo off
REM ==== maple REMOTE mode: Cloudflare Quick Tunnel + short-lived password ====
REM ---- Force run as Administrator (needed to focus/input to elevated game windows) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [maple] Requesting administrator privileges...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  Main token (for yourself over Tailscale; always valid).
set "MAPLE_TOKEN=***REMOVED***"
REM  Guest short-password lifetime in hours (default 0.5; can be
REM  extended anytime from the web control center, +0.5h steps).
set "MAPLE_REMOTE_TTL=0.5"
REM ============================================================

echo [maple] Ensuring single instance (closing any existing)...

REM 1) Kill MediaMTX / cloudflared (and their child processes)
taskkill /F /T /IM mediamtx.exe   >nul 2>&1
taskkill /F /T /IM cloudflared.exe >nul 2>&1

REM 2) Kill whatever is listening on our ports, WITH /T so the FastAPI process tree
REM    (its child ffmpeg) also dies -> frees COM3 and cleans up ffmpeg.
for %%P in (8000 8554 8889 8189 9997 8892) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /T /PID %%I >nul 2>&1
  )
)

timeout /t 2 /nobreak >nul

REM 3) Verify port 8000 is now free; abort if a stubborn instance remains
for /f "tokens=5" %%I in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
  echo [ERROR] Port 8000 still held by PID %%I after cleanup.
  echo         Close that window / end that process in Task Manager, then retry.
  pause & exit /b 1
)

if "%MAPLE_TOKEN%"=="change-me-please" echo [WARN] You are still using the default password. Edit MAPLE_TOKEN at the top of this file.

echo [maple] Starting REMOTE mode (tunnel URL + short password will be shown below)...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\remote.ps1"

echo [maple] Cleaning up...
taskkill /F /T /IM cloudflared.exe >nul 2>&1
taskkill /F /T /IM mediamtx.exe    >nul 2>&1
endlocal
