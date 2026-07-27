# 以管理員權限強制關閉舊進程並用最新程式碼重啟(UAC 關閉時靜默提權)
#
# $root 由腳本自身位置推得,不寫死絕對路徑 —— 先前寫死 C:\Users\mense\dev\maple,
# 專案搬到 D:\Project\maplestory_automation 之後那個路徑已不存在,而 $ErrorActionPreference
# 又是 SilentlyContinue,結果是「殺掉伺服器 → 靜默啟不起來」,比不執行還糟。
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$log = Join-Path $root "scratch_restart.log"
$token = "***REMOVED***"          # 與 start.bat 的 MAPLE_TOKEN 保持一致
$out = New-Object System.Collections.ArrayList
function Say($m) { Write-Host $m; [void]$out.Add($m) }

Say "[restart] root = $root"

# 前置檢查:缺東西就別殺伺服器(寧可什麼都不做,也不要殺完起不來)
foreach ($rel in @("bin\mediamtx.exe", "venv\Scripts\python.exe", "server\main.py")) {
  if (-not (Test-Path (Join-Path $root $rel))) {
    Say "[restart] FAIL 缺少 $rel — 不動舊進程,直接結束"
    $out | Out-File $log -Encoding utf8
    exit 1
  }
}

# 停止舊進程。【不用 Get-Process python | Stop-Process】—— 那會連使用者其他無關的
# Python 一起殺掉。改成:(1) 佔用本專案埠號的進程樹 (2) 本專案 venv 的 python
# (3) 依名稱的輔助進程。Stop-Process 而非 taskkill:taskkill 在部分環境下會回
# "ERROR: Not found" 但進程其實還活著。
foreach ($port in 8000, 8554, 8889, 8189, 9997, 8892) {
  $hits = netstat -ano | Select-String ":$port\s" | Select-String "LISTENING"
  foreach ($h in $hits) {
    $procId = ($h -split '\s+')[-1]
    if ($procId -match '^\d+$') {
      try { Stop-Process -Id $procId -Force -ErrorAction Stop; Say "[restart] 停止 PID $procId (port $port)" }
      catch { Say "[restart] 停止 PID $procId 失敗: $($_.Exception.Message)" }
    }
  }
}
$venvPy = Join-Path $root "venv\Scripts\python.exe"
Get-Process python, pythonw -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq $venvPy } |
  ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Say "[restart] 停止本專案 python PID $($_.Id)" } catch {} }
foreach ($n in @("mediamtx", "ffmpeg", "cloudflared")) {
  Get-Process $n -ErrorAction SilentlyContinue |
    ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Say "[restart] 停止 $n PID $($_.Id)" } catch {} }
}

Start-Sleep -Seconds 3
$held = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"
if ($held) {
  Say "[restart] FAIL port 8000 仍被佔用,放棄啟動(避免起第二份綁不到埠):$held"
  $out | Out-File $log -Encoding utf8
  exit 1
}

Say "[restart] 啟動 MediaMTX"
Start-Process -FilePath (Join-Path $root "bin\mediamtx.exe") `
              -ArgumentList (Join-Path $root "media\mediamtx.yml") `
              -WorkingDirectory $root -WindowStyle Hidden
Say "[restart] 啟動 FastAPI"
$env:MAPLE_TOKEN = $token
Start-Process -FilePath $venvPy -ArgumentList "server\main.py" `
              -WorkingDirectory $root -WindowStyle Minimized

# 輪詢而非固定 sleep:冷啟(WGC/序列埠/模型載入)有時要十幾秒,固定 6 秒會誤報 FAIL
$deadline = (Get-Date).AddSeconds(30)
$ok = $false
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8000/status?token=$token" -UseBasicParsing -TimeoutSec 4
    if ($r.StatusCode -eq 200) { $ok = $true; Say "[restart] OK 伺服器已回應"; break }
  } catch { }
}
if (-not $ok) { Say "[restart] FAIL 伺服器 30 秒內沒有回應" }
$out | Out-File $log -Encoding utf8
if ($ok) { exit 0 } else { exit 1 }
