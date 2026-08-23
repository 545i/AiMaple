# 以管理員權限強制關閉舊進程並用最新程式碼重啟(UAC 關閉時靜默提權)
#
# $root 由腳本自身位置推得,不寫死絕對路徑 —— 先前寫死 C:\Users\mense\dev\maple,
# 專案搬到 D:\Project\maplestory_automation 之後那個路徑已不存在,而 $ErrorActionPreference
# 又是 SilentlyContinue,結果是「殺掉伺服器 → 靜默啟不起來」,比不執行還糟。
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$log = Join-Path $root "scratch_restart.log"
$out = New-Object System.Collections.ArrayList
function Say($m) { Write-Host $m; [void]$out.Add($m) }

Say "[restart] root = $root"

# token 不寫死在版控裡,改讀 local-token.txt(已 gitignore)。
#
# 【為什麼】這個 token 是 _check_owner 的憑證 —— 拿到它加上遠端網址,就能透過
# Arduino HID 完全控制這台機器的鍵盤與滑鼠。寫死在追蹤檔案裡的話,推上任何遠端
# (即使是私有倉庫)都會連同【整個 git 歷史】一起帶走,事後刪檔案也撈得回來。
#
# 【為什麼缺檔就不啟動】寧可什麼都不做,也不要「殺掉舊行程 → 起來卻沒有認證」。
# 這支腳本開頭那段註解記著同一個教訓的另一個版本(路徑寫死 + SilentlyContinue
# 造成殺完起不來)。
$tokenFile = Join-Path $root "local-token.txt"
if (-not (Test-Path $tokenFile)) {
  Say "[restart] 找不到 $tokenFile。請建立它並放入一行 token(見 README)。不啟動。"
  $out -join "`n" | Out-File -FilePath $log -Encoding utf8
  exit 1
}
$token = (Get-Content $tokenFile -Raw).Trim()
if (-not $token) {
  Say "[restart] local-token.txt 是空的。不啟動。"
  $out -join "`n" | Out-File -FilePath $log -Encoding utf8
  exit 1
}

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
# 【stdout/stderr 一律導到檔案,不要留主控台】2026-08-23 實測:伺服器在 02:17 整個
# 卡死約 9 小時 —— 進程活著、port 8000 還在監聽,但 10 條執行緒全部 Wait、其中一條
# 的 wait reason 是 EventPairLow(卡在 Windows 主控台 I/O 的特徵)。死前 /exp/status
# 的延遲一路爆走 1005→2940→4884→23804ms,正是「主控台寫入逐漸阻塞」的曲線。
# 這個坑 main.py 與 restart-admin.bat 的註解都記過(視窗被點進 QuickEdit 選取模式
# 就會擋住寫入),但當時只有 PowerShell 階段導向檔案,FastAPI 自己仍然開著一個
# -WindowStyle Minimized 的真主控台在收 print。沒有主控台就沒有這條阻塞路徑。
#
# 【一定要加 -u】導向檔案後 Python 的 stdout 會變成整塊緩衝(4~8KB),日誌要等湊滿
# 才寫得出來,出事時看到的會是舊資料。-u 關掉緩衝,行為與原本的主控台一致。
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$srvOut = Join-Path $logDir "server_out.log"
$srvErr = Join-Path $logDir "server_err.log"
# 輪替:重導向是覆寫,但長期跑仍可能單檔膨脹,超過 8MB 先留一份再蓋。
foreach ($f in @($srvOut, $srvErr)) {
  if ((Test-Path $f) -and ((Get-Item $f).Length -gt 8MB)) {
    Move-Item $f "$f.1" -Force -ErrorAction SilentlyContinue
  }
}
Start-Process -FilePath $venvPy -ArgumentList "-u", "server\main.py" `
              -WorkingDirectory $root -WindowStyle Hidden `
              -RedirectStandardOutput $srvOut -RedirectStandardError $srvErr
Say "[restart] 伺服器輸出 -> logs\server_out.log / server_err.log"

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
