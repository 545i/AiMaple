# 以管理員權限強制關閉舊進程並用最新程式碼重啟(UAC 關閉時靜默提權)
$ErrorActionPreference = "SilentlyContinue"
$root = "C:\Users\mense\dev\maple"
Get-Process python,pythonw,mediamtx,ffmpeg,cloudflared | Stop-Process -Force
Start-Sleep -Seconds 3
Set-Location $root
Start-Process -FilePath "$root\bin\mediamtx.exe" -ArgumentList "media\mediamtx.yml" -WorkingDirectory $root -WindowStyle Hidden
$env:MAPLE_TOKEN = "***REMOVED***"
Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList "server\main.py" -WorkingDirectory $root -WindowStyle Minimized
Start-Sleep -Seconds 6
$log = "$root\scratch_restart.log"
try {
  $r = (Invoke-WebRequest "http://127.0.0.1:8000/status?token=***REMOVED***" -UseBasicParsing).Content
  "OK " + $r | Out-File $log -Encoding utf8
} catch {
  "FAIL server not responding" | Out-File $log -Encoding utf8
}
