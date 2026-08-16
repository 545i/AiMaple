# 打包成「一個 EXE + 一個素材夾」。從專案任何位置執行皆可(路徑由腳本自身推得)。
#
# 流程:
#   1. 用 arduino-cli 把 .ino 編成 .hex(只有開發機需要 271MB 的 AVR 工具鏈)
#   2. PyInstaller 依 MapleAuto.spec 產生單一 exe(web/、韌體、模板都內嵌)
#   3. 組出 assets\:第三方大位元 + 可寫資料目錄
#
# 產出:dist\MapleAuto\MapleAuto.exe + dist\MapleAuto\assets\
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
function Say($m) { Write-Host "[build] $m" }

$py = Join-Path $root "venv\Scripts\python.exe"
$cli = Join-Path $root "bin\arduino-cli.exe"
foreach ($p in @($py, $cli)) {
  if (-not (Test-Path $p)) { throw "缺少 $p" }
}

# ---------- 1. 韌體 ----------
Say "編譯 Arduino 韌體 -> firmware\arduino_kbm.ino.hex"
# --libraries 指到專案內的 libraries:使用者的 Arduino 程式庫在 OneDrive 中文路徑下,
# 編譯器吃不到(實測 fatal error: Keyboard.h: No such file or directory)。
& $cli compile --fqbn arduino:avr:leonardo `
  --libraries (Join-Path $root "bin\arduino-user\libraries") `
  (Join-Path $root "firmware\arduino_kbm") `
  --output-dir (Join-Path $root "firmware") | Out-Null
if ($LASTEXITCODE -ne 0) { throw "韌體編譯失敗" }
# 只留燒錄需要的 .hex,其他中間產物不必進 exe
Get-ChildItem (Join-Path $root "firmware") -File |
  Where-Object { $_.Name -match '\.(eep|elf)$' -or $_.Name -like '*with_bootloader*' } |
  Remove-Item -Force -EA SilentlyContinue
$hex = Join-Path $root "firmware\arduino_kbm.ino.hex"
if (-not (Test-Path $hex)) { throw "找不到編譯結果 $hex" }
Say ("韌體 " + [math]::Round((Get-Item $hex).Length / 1KB, 1) + " KB")

# ---------- 2. avrdude 就位(素材夾要用) ----------
$av = Join-Path $env:LOCALAPPDATA "Arduino15\packages\arduino\tools\avrdude"
$avdir = Get-ChildItem $av -Directory -EA SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
if ($avdir) {
  New-Item -ItemType Directory -Force (Join-Path $root "bin\avrdude\bin") | Out-Null
  New-Item -ItemType Directory -Force (Join-Path $root "bin\avrdude\etc") | Out-Null
  Copy-Item (Join-Path $avdir.FullName "bin\avrdude.exe") (Join-Path $root "bin\avrdude\bin") -Force
  Copy-Item (Join-Path $avdir.FullName "etc\avrdude.conf") (Join-Path $root "bin\avrdude\etc") -Force
  Say "avrdude 已就位($($avdir.Name))"
} elseif (Test-Path (Join-Path $root "bin\avrdude\bin\avrdude.exe")) {
  Say "avrdude 已存在,沿用"
} else {
  throw "找不到 avrdude(先用 arduino-cli 安裝 arduino:avr core)"
}

# ---------- 3. PyInstaller ----------
Say "PyInstaller 打包(單一 exe)"
Remove-Item (Join-Path $root "build") -Recurse -Force -EA SilentlyContinue
Remove-Item (Join-Path $root "dist") -Recurse -Force -EA SilentlyContinue
& $py -m PyInstaller --noconfirm --clean (Join-Path $root "MapleAuto.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗" }
$exe = Join-Path $root "dist\MapleAuto.exe"
if (-not (Test-Path $exe)) { throw "找不到 dist\MapleAuto.exe" }

# ---------- 4. 組素材夾 ----------
$out = Join-Path $root "dist\MapleAuto"
$assets = Join-Path $out "assets"
New-Item -ItemType Directory -Force $assets | Out-Null
Move-Item $exe (Join-Path $out "MapleAuto.exe") -Force

Say "複製第三方執行檔 -> assets\bin"
New-Item -ItemType Directory -Force (Join-Path $assets "bin") | Out-Null
foreach ($item in @("mediamtx.exe", "cloudflared.exe", "avrdude")) {
  $src = Join-Path $root "bin\$item"
  if (Test-Path $src) { Copy-Item $src (Join-Path $assets "bin") -Recurse -Force }
  else { Say "  略過(不存在):bin\$item" }
}
# ffmpeg 只帶 ffmpeg.exe。整個資料夾 406MB,其中 ffplay.exe 與 ffprobe.exe 各 138MB
# 本專案完全沒用到(全域搜尋只出現 ffmpeg.exe),doc 另外 11MB -> 共省 276MB。
# 目錄層次必須保留 bin/ffmpeg/bin/ffmpeg.exe:video_pipeline 與 audio_pipeline 寫死這個相對路徑。
$ffDst = Join-Path $assets "bin\ffmpeg\bin"
New-Item -ItemType Directory -Force $ffDst | Out-Null
Copy-Item (Join-Path $root "bin\ffmpeg\bin\ffmpeg.exe") $ffDst -Force
Copy-Item (Join-Path $root "bin\ffmpeg\LICENSE.txt") (Join-Path $assets "bin\ffmpeg") -Force -EA SilentlyContinue
Say "  ffmpeg:只帶 ffmpeg.exe(不含 ffplay/ffprobe/doc)"

Say "複製 media\mediamtx.yml"
New-Item -ItemType Directory -Force (Join-Path $assets "media") | Out-Null
Copy-Item (Join-Path $root "media\mediamtx.yml") (Join-Path $assets "media") -Force -EA SilentlyContinue

# 可寫資料目錄:建空的就好。刻意【不】複製開發機的設定 ——
# 那是開發者自己的巡邏點/資料集,不該跟著散出去。
foreach ($d in @("layouts", "profiles", "rune_dataset", "logs")) {
  New-Item -ItemType Directory -Force (Join-Path $assets $d) | Out-Null
}

# ---------- 5. 報告 ----------
$exeMB = [math]::Round((Get-Item (Join-Path $out "MapleAuto.exe")).Length / 1MB, 1)
$asMB = [math]::Round(((Get-ChildItem $assets -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Say "完成"
Say "  MapleAuto.exe   $exeMB MB"
Say "  assets\         $asMB MB"
Get-ChildItem (Join-Path $assets "bin") | ForEach-Object { Say ("    bin\" + $_.Name) }
