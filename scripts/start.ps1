# maple 一鍵啟動：MediaMTX(WebRTC 影像) + FastAPI(輸入/網頁)
# 用法：於 maple 根目錄執行  ./scripts/start.ps1
$ErrorActionPreference = "Stop"
$maple = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $maple

if (-not $env:MAPLE_TOKEN) {
  Write-Host "⚠ 未設定 MAPLE_TOKEN，將使用預設 'change-me-please'（正式使用請務必設定）" -ForegroundColor Yellow
}
if (-not (Test-Path "bin\mediamtx.exe"))          { Write-Error "缺少 bin\mediamtx.exe，請先執行 scripts\fetch-bin.ps1"; exit 1 }
if (-not (Test-Path "bin\ffmpeg\bin\ffmpeg.exe")) { Write-Error "缺少 ffmpeg，請先執行 scripts\fetch-bin.ps1"; exit 1 }
if (-not (Test-Path "venv\Scripts\python.exe"))   { Write-Error "缺少 venv，請先 python -m venv venv 並 pip install -r requirements.txt"; exit 1 }

# 啟動 MediaMTX（工作目錄 = maple 根，讓 ffmpeg 相對路徑正確）
$mtx = Start-Process -FilePath ".\bin\mediamtx.exe" -ArgumentList "media\mediamtx.yml" `
        -WorkingDirectory $maple -PassThru -WindowStyle Minimized
Write-Host "▶ MediaMTX 已啟動 (PID $($mtx.Id))  WebRTC :8889 / RTSP :8554" -ForegroundColor Green

try {
  Write-Host "▶ 啟動 FastAPI (:8000)  —  Ctrl+C 結束" -ForegroundColor Green
  & ".\venv\Scripts\python.exe" "server\main.py"
} finally {
  Write-Host "■ 停止 MediaMTX..." -ForegroundColor Yellow
  Stop-Process -Id $mtx.Id -Force -ErrorAction SilentlyContinue
}
