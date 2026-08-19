# 以管理員權限強制關閉本專案的進程(python 輸入伺服器 / mediamtx / ffmpeg / cloudflared)
# 用途：滑鼠被伺服器控制、進程殺不掉時，用這個徹底停止並釋放 COM3。
#
# 【不寫死絕對路徑】先前寫死 C:\Users\mense\dev\maple\scratch_kill.log，專案搬到
# D:\Project\maplestory_automation 之後那個目錄已不存在，Out-File 直接拋錯，
# 使用者關掉視窗時什麼結果都看不到。root 一律由腳本自身位置推得
# (restart-admin.ps1 開頭記著同一個教訓的另一個版本)。
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$log  = Join-Path $root "scratch_kill.log"
$out  = New-Object System.Collections.ArrayList
function Say($m) { Write-Host $m; [void]$out.Add($m) }

Say "[kill] root = $root"

# 【不用 Get-Process python | Stop-Process】—— 那會連使用者其他無關的 Python
# (訓練腳本、Jupyter、別的專案) 一起殺掉。改成:(1) 佔用本專案埠號的進程
# (2) 本專案 venv 的 python (3) 依名稱的輔助進程。與 restart-admin.ps1 一致。
foreach ($port in 8000, 8554, 8889, 8189, 9997, 8892) {
  $hits = netstat -ano | Select-String ":$port\s" | Select-String "LISTENING"
  foreach ($h in $hits) {
    $procId = ($h -split '\s+')[-1]
    if ($procId -match '^\d+$') {
      try { Stop-Process -Id $procId -Force -ErrorAction Stop; Say "[kill] 停止 PID $procId (port $port)" }
      catch { Say "[kill] 停止 PID $procId 失敗: $($_.Exception.Message)" }
    }
  }
}

$venvPy = Join-Path $root "venv\Scripts\python.exe"
Get-Process python, pythonw -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq $venvPy } |
  ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Say "[kill] 停止本專案 python PID $($_.Id)" } catch {} }

foreach ($n in @("mediamtx", "ffmpeg", "cloudflared")) {
  Get-Process $n -ErrorAction SilentlyContinue |
    ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Say "[kill] 停止 $n PID $($_.Id)" } catch {} }
}

Start-Sleep -Seconds 1
$listen = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
Say ("[kill] maple stopped. 8000: " + $(if ($listen) { "still PID " + $listen.OwningProcess } else { "free OK" }))
$out | Out-File -FilePath $log -Encoding utf8
if ($listen) { exit 1 } else { exit 0 }
