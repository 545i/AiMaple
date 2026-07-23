# maple 遠端訪客模式一鍵啟動：
#   MAPLE_REMOTE=1 → FastAPI 啟動時自動「產生 8 碼短密碼 + 啟動 Cloudflare Quick Tunnel」，
#   密碼與 https://xxx.trycloudflare.com 網址都會印在下方主控台。
#   訪客僅能按 4 / ← / → 螢幕按鈕（伺服器端白名單），滑鼠與其他操作全面封鎖。
#   平常跑 start.ps1 時也可從網頁「控制中心 → 遠端分享」隨時開關隧道/管理密碼。
# 用法：於 maple 根目錄執行  ./scripts/remote.ps1   （或用根目錄 remote.bat）
$ErrorActionPreference = "Stop"
$maple = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $maple

if (-not $env:MAPLE_TOKEN) {
  Write-Host "⚠ 未設定 MAPLE_TOKEN，將使用預設 'change-me-please'（正式使用請務必設定）" -ForegroundColor Yellow
}
if (-not (Test-Path "bin\mediamtx.exe"))          { Write-Error "缺少 bin\mediamtx.exe，請先執行 scripts\fetch-bin.ps1"; exit 1 }
if (-not (Test-Path "bin\ffmpeg\bin\ffmpeg.exe")) { Write-Error "缺少 ffmpeg，請先執行 scripts\fetch-bin.ps1"; exit 1 }
if (-not (Test-Path "bin\cloudflared.exe"))       { Write-Error "缺少 bin\cloudflared.exe，請先執行 scripts\fetch-bin.ps1"; exit 1 }
if (-not (Test-Path "venv\Scripts\python.exe"))   { Write-Error "缺少 venv，請先 python -m venv venv 並 pip install -r requirements.txt"; exit 1 }

# 遠端訪客模式：短密碼預設 0.5 小時（可於控制中心 +0.5h / 自訂延長）
$env:MAPLE_REMOTE = "1"
if (-not $env:MAPLE_REMOTE_TTL) { $env:MAPLE_REMOTE_TTL = "0.5" }

# 啟動 MediaMTX（在家 Tailscale 同時連線仍可用 WebRTC 低延遲；遠端訪客走 MJPEG）
$mtx = Start-Process -FilePath ".\bin\mediamtx.exe" -ArgumentList "media\mediamtx.yml" `
        -WorkingDirectory $maple -PassThru -WindowStyle Minimized
Write-Host "▶ MediaMTX 已啟動 (PID $($mtx.Id))" -ForegroundColor Green

try {
  Write-Host "▶ 啟動 FastAPI (:8000) — 遠端網址與密碼會印在下方 — Ctrl+C 結束" -ForegroundColor Green
  & ".\venv\Scripts\python.exe" "server\main.py"
} finally {
  Write-Host "■ 停止 MediaMTX 與 cloudflared..." -ForegroundColor Yellow
  Stop-Process -Id $mtx.Id -Force -ErrorAction SilentlyContinue
  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
