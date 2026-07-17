# 以管理員權限強制關閉所有 maple 相關進程(python 輸入伺服器 / mediamtx / ffmpeg)
# 用途：滑鼠被伺服器控制、進程殺不掉時，用這個徹底停止。
Get-Process python,pythonw,mediamtx,ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$listen = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
"maple stopped. 8000: " + ($(if($listen){"still PID " + $listen.OwningProcess}else{"free OK"})) |
  Out-File -FilePath "C:\Users\mense\dev\maple\scratch_kill.log" -Encoding utf8
