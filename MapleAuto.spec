# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包規格。用 scripts\\build.ps1 執行(它會先編韌體再叫 PyInstaller)。

--------------------------------------------------------------------------
產出版面(與使用者約定:一個 EXE + 一個素材夾)
--------------------------------------------------------------------------
    dist\\MapleAuto\\
      MapleAuto.exe      ← 這裡:Python 程式碼、web UI、韌體 .hex/.ino、模板圖
      assets\\           ← build.ps1 組出來:bin/ media/ layouts/ profiles/ ...

--------------------------------------------------------------------------
什麼進 exe、什麼放素材夾
--------------------------------------------------------------------------
進 exe(唯讀、且不希望使用者亂改或外流):
    web/                 中控頁
    firmware/            韌體 .hex + .ino 原始碼 ★使用者要求不可外放
    server/rune_arrow_tpl.png   符文判向模板
    server/rune_capsule_tpl.png 符文膠囊定位模板(邊緣形狀)
    server/km.dll        KMBox 驅動(ctypes 載入,必須跟得到)

放素材夾(第三方大位元 或 必須可寫):
    bin/                 mediamtx 55MB / ffmpeg / cloudflared 54MB / avrdude 6.7MB
                         → 全塞進 exe 會讓每次啟動解壓 170MB(實測 10~30s)
    media/mediamtx.yml   使用者可能要調
    layouts/ profiles/   ★可寫:巡邏點、地形、平A、具名設置
    rune_dataset/        ★可寫:符文自動標註樣本(會持續長大)
    logs/                ★可寫:移動記錄、校準資料

【為什麼可寫資料不能進 exe】onefile 模式解壓到暫存目錄,寫進去的東西關掉就消失 ——
使用者設好的巡邏點會「自己不見」而且沒有任何錯誤訊息。見 server/paths.py。
"""
import os

ROOT = os.path.abspath(SPECPATH)
SRV = os.path.join(ROOT, "server")

datas = [
    (os.path.join(ROOT, "web"), "web"),
    # 韌體:.hex(燒錄用)+ sketch 原始碼(中控頁顯示標題/版本)
    (os.path.join(ROOT, "firmware", "arduino_kbm.ino.hex"), "firmware"),
    (os.path.join(ROOT, "firmware", "arduino_kbm"), os.path.join("firmware", "arduino_kbm")),
    (os.path.join(SRV, "rune_arrow_tpl.png"), "."),
    (os.path.join(SRV, "rune_capsule_tpl.png"), "."),
]
# km.dll 用 ctypes 載入,PyInstaller 掃不到 → 必須手動列為 binaries
binaries = []
_km = os.path.join(SRV, "km.dll")
if os.path.exists(_km):
    binaries.append((_km, "."))

hiddenimports = [
    # uvicorn 用字串動態載入這些,靜態分析看不到
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    # 本專案的延遲匯入(函式內 import,分析器抓不到)
    "frames", "audio_pipeline", "mapdata", "minimap", "pathgraph",
    "rune_cv", "firmware", "paths", "revive", "cpu_tune", "wgc",
    "serial.tools.list_ports",
]

excludes = [
    # 打包用不到卻很肥的東西。torch 是先前試本地 VLM 留下的,已移除但保險起見排除。
    "torch", "transformers", "matplotlib", "tkinter", "PIL.ImageTk",
    "pytest", "IPython", "notebook",
]

a = Analysis(
    [os.path.join(SRV, "main.py")],
    pathex=[SRV],                  # server/ 內的模組互相用 `import xxx` 平行匯入
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="MapleAuto",
    debug=False,
    strip=False,
    upx=False,                     # UPX 壓縮過的 exe 常被防毒誤判,得不償失
    console=True,                  # 保留主控台:啟動訊息/錯誤/遠端密碼都印在這
    icon=None,
)
# 【沒有 COLLECT 就是 onefile】PyInstaller 的 EXE() 沒有 onefile 參數,
# 單檔與資料夾模式的差別在於「有沒有再寫一個 COLLECT」。這裡刻意不寫。
