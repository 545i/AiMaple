# -*- coding: utf-8 -*-
"""資源與資料路徑的【單一來源】。開發直跑與 PyInstaller 打包後都用同一組函式。

--------------------------------------------------------------------------
為什麼需要這個模組
--------------------------------------------------------------------------
原本各模組都自己用 `os.path.dirname(__file__)` 往上推路徑。打包成 exe 後這會壞掉,
而且是【兩種不同的壞法】,必須分開處理:

  * 唯讀資源(web UI、韌體 .hex、模板圖):PyInstaller 會解壓到暫存目錄 `sys._MEIPASS`,
    `__file__` 指到那裡是對的 —— 但那個目錄每次執行都不同、關掉就消失。
  * 可寫資料(設定 layouts/、資料集、日誌):寫進 `_MEIPASS` 等於寫進暫存目錄,
    下次啟動就不見了 —— 使用者的巡邏點設定會「自己消失」,而且不會有任何錯誤訊息。
    這類資料必須放在 exe 旁邊的素材資料夾。

--------------------------------------------------------------------------
打包後的實際版面(與使用者約定)
--------------------------------------------------------------------------
    MapleAuto\\
      MapleAuto.exe        ← Python 程式碼、web UI、韌體 .hex/.ino(唯讀,內嵌)
      assets\\
        bin\\              ← mediamtx / ffmpeg / cloudflared / avrdude(第三方大位元)
        media\\            ← mediamtx.yml
        layouts\\          ← 使用者設定(巡邏點/地形/平A/放置技能)  ★可寫
        profiles\\         ← 具名設置                              ★可寫
        rune_dataset\\     ← 符文自動標註樣本                      ★可寫
        logs\\             ← 移動記錄、校準資料                    ★可寫

開發直跑時 assets 就是專案根目錄(bin/、layouts/… 本來就在那裡),所以兩種模式的
相對結構一致,不必在各模組裡寫 if frozen。
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)

# ---------- 唯讀資源(內嵌在 exe 裡) ----------
if FROZEN:
    # PyInstaller 解壓目錄。onefile 是 sys._MEIPASS,onedir 是 exe 所在目錄。
    _RES = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    _RES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def res(*parts):
    """唯讀資源路徑(web/、firmware/、模板圖)。打包後在 exe 內部。"""
    return os.path.join(_RES, *parts)


# ---------- 素材資料夾(可寫、與 exe 並列) ----------
def _assets_root():
    """素材資料夾。可用 MAPLE_ASSETS 環境變數覆寫(綠色版放隨身碟、或多套設定切換)。"""
    env = os.environ.get("MAPLE_ASSETS")
    if env:
        return os.path.abspath(env)
    if FROZEN:
        return os.path.join(os.path.dirname(sys.executable), "assets")
    # 開發直跑:專案根目錄本身就是素材夾(bin/ layouts/ media/ 都在那)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ASSETS = _assets_root()


def asset(*parts, mkdir=False):
    """素材/資料路徑。mkdir=True 會確保父目錄存在(寫檔前用)。"""
    p = os.path.join(ASSETS, *parts)
    if mkdir:
        os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


def data_dir(name):
    """可寫資料目錄(不存在就建)。layouts / profiles / rune_dataset / logs。"""
    p = os.path.join(ASSETS, name)
    os.makedirs(p, exist_ok=True)
    return p


# ---------- 常用路徑 ----------
def web_dir():
    return res("web")


def srv_res(name):
    """放在 server\\ 裡的唯讀資源(符文模板、km.dll)。

    開發時它們就在 server\\ 底下;打包後 PyInstaller 把它們放在 bundle 根目錄。
    兩種模式的相對位置不同,所以不能直接用 res() —— 這也是實際踩到的:
    模板路徑指錯會讓判向靜默退回啟發式(正確率 100% → 99%),不會有任何錯誤訊息。"""
    return res(name) if FROZEN else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def bin_path(*parts):
    return os.path.join(ASSETS, "bin", *parts)


def firmware_hex():
    """預先編好的韌體。刻意【內嵌在 exe】而非放素材夾 —— 使用者要求韌體不可外放。"""
    return res("firmware", "arduino_kbm.ino.hex")


def firmware_src():
    """韌體原始碼(同樣內嵌)。中控頁可顯示版本/內容,但不落地成可改的檔案。
    原始碼在 sketch 子目錄裡(arduino-cli 要求資料夾名 == .ino 檔名)。"""
    return res("firmware", "arduino_kbm", "arduino_kbm.ino")


def describe():
    """給 /status 與診斷用:目前用的是哪一組路徑。"""
    return {"frozen": FROZEN, "res": _RES, "assets": ASSETS,
            "web": web_dir(), "bin": bin_path()}
