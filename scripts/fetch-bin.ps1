# 下載 MediaMTX 與 ffmpeg(含 NVENC) 到 bin\（這些二進位不進版控）
# 用法：./scripts/fetch-bin.ps1
$ErrorActionPreference = "Stop"
$maple = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bin = Join-Path $maple "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Set-Location $bin

# --- MediaMTX ---
if (-not (Test-Path "mediamtx.exe")) {
  $ver = "v1.19.2"
  $url = "https://github.com/bluenviron/mediamtx/releases/download/$ver/mediamtx_${ver}_windows_amd64.zip"
  Write-Host "下載 MediaMTX $ver ..."
  Invoke-WebRequest -Uri $url -OutFile "mediamtx.zip"
  Expand-Archive -Path "mediamtx.zip" -DestinationPath . -Force
  Remove-Item "mediamtx.zip"
}

# --- ffmpeg 7.1（對應 NVENC API 13.0；若你的 NVIDIA 驅動 >=610 可改用 master 版）---
if (-not (Test-Path "ffmpeg\bin\ffmpeg.exe")) {
  $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-gpl-7.1.zip"
  Write-Host "下載 ffmpeg 7.1 (含 nvenc) ..."
  Invoke-WebRequest -Uri $url -OutFile "ffmpeg.zip"
  Expand-Archive -Path "ffmpeg.zip" -DestinationPath . -Force
  $d = Get-ChildItem -Directory -Filter "ffmpeg-n7.1*" | Select-Object -First 1
  if (Test-Path "ffmpeg") { Remove-Item "ffmpeg" -Recurse -Force }
  Rename-Item $d.FullName "ffmpeg"
  Remove-Item "ffmpeg.zip"
}

# --- cloudflared（遠端模式：Cloudflare Quick Tunnel）---
if (-not (Test-Path "cloudflared.exe")) {
  $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
  Write-Host "下載 cloudflared ..."
  Invoke-WebRequest -Uri $url -OutFile "cloudflared.exe"
}

Write-Host "✔ 完成。bin\mediamtx.exe、bin\ffmpeg\bin\ffmpeg.exe 與 bin\cloudflared.exe 就緒。" -ForegroundColor Green
