# -*- coding: utf-8 -*-
"""把浮動客戶端的安裝包做成可從網頁下載的檔案。

【為什麼需要這個】`client/` 的 Electron 客戶端是裝在【操控端】的(見
docs/superpowers/specs/2026-08-17-floating-client-design.md),但它建置出來的產物躺在
遊戲那台的專案目錄裡。使用者在手機或另一台電腦上打開遠端網頁時,沒有任何地方拿得到
它 —— 那等於這個客戶端只有開發者能用。

【為什麼要壓縮而不是直接給資料夾】electron-builder 在這台只做出 `dist/win-unpacked/`
(單檔 portable 那步因為缺 SeCreateSymbolicLinkPrivilege 失敗,見 client/README.md),
那是一個 325MB 的資料夾,HTTP 沒辦法「下載一個資料夾」。所以壓成一個 zip。

【為什麼是背景執行緒而不是請求裡直接壓】325MB 壓縮要數十秒,擋在請求裡會讓瀏覽器
逾時、也會卡住 uvicorn 的執行緒。改成:第一次問的時候回「尚未準備」,由前端觸發
prepare 開一條背景執行緒,前端輪詢狀態,好了再給下載連結。

【為什麼要比對 mtime】重新建置客戶端之後,舊的 zip 會變成過期的假貨,而使用者不會知道
自己下載到的是舊版。所以把來源最新的 mtime 記在旁邊的 .stamp,不一致就重壓。
"""
import os
import threading
import zipfile

import paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = paths.data_dir("client_pkg")

# 各平台的來源目錄與產出檔名。只有 Windows 有東西 —— macOS 的 dmg 必須在 Mac 上
# 建置,Linux 的 AppImage/deb 也要在 Linux 上建,開發機是 Windows 所以做不出來。
# 這裡如實把它們列成「未建置」,而不是假裝有。
TARGETS = {
    "win": {
        "src": os.path.join(ROOT, "client", "dist", "win-unpacked"),
        "zip": "maple-float-client-win.zip",
        "label": "Windows(免安裝,解壓後執行 exe)",
    },
    "linux": {
        "src": os.path.join(ROOT, "client", "dist", "linux-unpacked"),
        "zip": "maple-float-client-linux.zip",
        "label": "Linux(AppImage / deb 需在 Linux 上建置)",
    },
    "mac": {
        "src": os.path.join(ROOT, "client", "dist", "mac"),
        "zip": "maple-float-client-mac.zip",
        "label": "macOS(dmg 需在 Mac 上建置)",
    },
}

_state = {}          # name -> {"building": bool, "err": str|None, "pct": int}
_lock = threading.Lock()


def _newest_mtime(src):
    """來源目錄裡最新的 mtime。用來判斷 zip 是不是過期的假貨。"""
    newest = 0.0
    for base, _dirs, files in os.walk(src):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(base, f)))
            except OSError:
                pass
    return newest


def _zip_path(name):
    return os.path.join(OUT_DIR, TARGETS[name]["zip"])


def _stamp_path(name):
    return _zip_path(name) + ".stamp"


def _fresh(name):
    """zip 存在且與來源同步。"""
    zp, sp = _zip_path(name), _stamp_path(name)
    if not (os.path.exists(zp) and os.path.exists(sp)):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            return abs(float(f.read().strip()) - _newest_mtime(TARGETS[name]["src"])) < 1.0
    except (OSError, ValueError):
        return False


def _build(name):
    """壓縮。跑在背景執行緒裡。"""
    src = TARGETS[name]["src"]
    zp = _zip_path(name)
    tmp = zp + ".part"        # 先寫暫存檔:壓到一半被中斷時不要留下半個 zip 冒充成品
    try:
        files = [os.path.join(b, f) for b, _d, fs in os.walk(src) for f in fs]
        total = max(1, len(files))
        # ZIP_DEFLATED 而不是 ZIP_STORED:Electron 的內容(主要是 .dll/.pak/.bin)壓得動,
        # 325MB 大約掉到一半,對走 Cloudflare Tunnel 的下載差別很有感。
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for i, p in enumerate(files):
                z.write(p, os.path.relpath(p, src))
                if i % 20 == 0:
                    with _lock:
                        _state[name]["pct"] = int(i * 100 / total)
        with open(_stamp_path(name), "w", encoding="utf-8") as f:
            f.write(str(_newest_mtime(src)))
        os.replace(tmp, zp)
        with _lock:
            _state[name].update(building=False, pct=100, err=None)
    except Exception as e:                      # noqa: BLE001 —— 背景執行緒不能讓例外消失
        try:
            os.path.exists(tmp) and os.remove(tmp)
        except OSError:
            pass
        with _lock:
            _state[name].update(building=False, err=f"{type(e).__name__}: {e}")


def prepare(name):
    """開始壓縮(已在壓或已是最新就不重複)。回目前狀態。"""
    if name not in TARGETS:
        return {"ok": False, "err": "未知的平台"}
    if not os.path.isdir(TARGETS[name]["src"]):
        return {"ok": False, "err": "這個平台還沒建置(見 client/README.md)"}
    with _lock:
        st = _state.setdefault(name, {"building": False, "err": None, "pct": 0})
        if st["building"]:
            return {"ok": True, **st}
        if _fresh(name):
            st.update(building=False, pct=100, err=None)
            return {"ok": True, **st}
        st.update(building=True, pct=0, err=None)
    threading.Thread(target=_build, args=(name,), daemon=True).start()
    return {"ok": True, "building": True, "pct": 0, "err": None}


def status():
    """所有平台的狀態,給前端列表用。"""
    out = []
    for name, t in TARGETS.items():
        zp = _zip_path(name)
        has_src = os.path.isdir(t["src"])
        with _lock:
            st = dict(_state.get(name, {"building": False, "err": None, "pct": 0}))
        ready = _fresh(name)
        out.append({
            "name": name, "label": t["label"], "built": has_src, "ready": ready,
            "stale": bool(os.path.exists(zp) and not ready and not st["building"]),
            "size": os.path.getsize(zp) if ready else 0,
            **st,
        })
    return out


def file_of(name):
    """可下載的 zip 路徑,沒準備好回 None。"""
    if name not in TARGETS or not _fresh(name):
        return None
    return _zip_path(name)


def filename_of(name):
    return TARGETS[name]["zip"] if name in TARGETS else None
