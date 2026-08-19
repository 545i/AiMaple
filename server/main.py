# -*- coding: utf-8 -*-
"""FastAPI 遠端遊玩伺服器。

跑在家裡的遊戲主機上：
  * 手機瀏覽器連進來 -> 看畫面(MJPEG) + 送輸入(WebSocket)
  * 鍵盤輸入 -> Arduino HID(序列埠)
  * 滑鼠輸入 -> km.dll(KMBox 硬體)
兩者皆為硬體訊號，遊戲/反作弊偵測不到是遠端輸入。

安全性：所有端點需帶 ?token=，且務必只透過 Tailscale/VPN 連線，勿裸奔公網。
"""
import cpu_tune          # 必須在 numpy/cv2 之前:限制執行緒數的環境變數要在它們載入前設好
import asyncio
import hmac
import ipaddress
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import (StreamingResponse, FileResponse, JSONResponse,
                               Response)

from config import (AUTH_TOKEN, HOST, PORT, TARGET_FPS, MOUSE_SENSITIVITY,
                    REMOTE_MODE, REMOTE_TTL_HOURS, GUEST_COOLDOWN, GUEST_MAX_CONN,
                    GUARD_EXE)
from arduino import ArduinoKeyboard
from kmbox import KMouse
from capture import ScreenCapture
from soft_mouse import wheel as soft_wheel
import video_pipeline
import clipboard
import remote_access
import tunnel
import wgc
import idle_mode
import mapdata
import navigator
import minimap
import calib
import rune
import rune_viz
import revive
import firmware
import paths
import exp
import jobs

# 走 paths 而非自己拼 ../web:打包成 exe 後 web/ 在 exe 內部(sys._MEIPASS),
# 不在 exe 旁邊,自己拼相對路徑會找不到頁面。
WEB = paths.web_dir()


def _disable_console_quickedit():
    """停用主控台 QuickEdit：避免不小心點到視窗就暫停整個程式（要按 Enter 才繼續）。"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)          # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_EXTENDED_FLAGS = 0x0080
            ENABLE_QUICK_EDIT_MODE = 0x0040
            ENABLE_MOUSE_INPUT = 0x0010
            new = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE & ~ENABLE_MOUSE_INPUT
            k.SetConsoleMode(h, new)
    except Exception:
        pass


_disable_console_quickedit()

keyboard = ArduinoKeyboard()
mouse = KMouse()
screen = ScreenCapture()


def _check_token_strength():
    """啟動時檢查 token(P0-4)。預設弱 token = 任何連得到本機的人都能操控電腦。
    預設只警告(不擋，避免破壞既有 start 流程)；設 MAPLE_STRICT_TOKEN=1 則直接拒啟。"""
    if AUTH_TOKEN != "change-me-please":
        return
    line = "=" * 60
    # 注意：訊息只用 ASCII 記號 + Big5 可編碼的中文，避免 Windows cp950 主控台
    # 遇到 emoji/符號(如 U+26A0)時 print 拋 UnicodeEncodeError 反而讓守衛崩潰。
    print(f"\n{line}\n[!] 警告：正在使用預設 token 'change-me-please'！\n"
          "    任何能連到本機(0.0.0.0)的人都能操控你的電腦。\n"
          "    請設定環境變數 MAPLE_TOKEN=<自訂隨機字串> 後重啟。\n"
          f"{line}\n")
    if os.environ.get("MAPLE_STRICT_TOKEN") == "1":
        raise RuntimeError("拒絕以預設 token 啟動(MAPLE_STRICT_TOKEN=1)")


def _print_remote_banner():
    """遠端模式開機自啟：產生短密碼並印出（有效 REMOTE_TTL_HOURS 小時）。"""
    import time as _t
    pw, exp = remote_access.generate(REMOTE_TTL_HOURS * 3600)
    line = "=" * 60
    # 同 _check_token_strength：只用 ASCII 記號，避免 cp950 主控台編碼炸掉
    print(f"\n{line}\n[*] 遠端訪客模式已啟用 (Cloudflare Tunnel)\n"
          f"    連線密碼: {pw}\n"
          f"    有效至  : {_t.strftime('%H:%M', _t.localtime(exp))}"
          f" (共 {REMOTE_TTL_HOURS:g} 小時，可於控制中心延長)\n"
          "    訪客僅能按 4 / 左 / 右 三鍵，滑鼠與其他操作一律封鎖\n"
          "    遠端網址稍後由 [tunnel] 印出 (https://xxx.trycloudflare.com)\n"
          f"{line}\n")


async def _rental_guard_loop():
    """出租守衛：短密碼有效期間,每秒——
    1) 強制鎖定「視窗模式 + MapleStory」(遊戲重開/換 hwnd 也會重新鎖上)
    2) 目標視窗失焦就強制切回前景(訪客輸入永遠只進遊戲,擷取畫面也不會被
       其他視窗蓋住)。密碼過期/撤銷後即完全不動作,不影響主人平時操作。"""
    def _tick():
        # 統一焦點守衛(video_pipeline.guard_focus):鎖定 MapleStory + 失焦切回。
        # 可能觸發 restart(數秒阻塞)、含 sleep,故丟執行緒池,不阻塞事件迴圈。
        video_pipeline.guard_focus(GUARD_EXE)

    loop = asyncio.get_event_loop()
    while True:
        try:
            # 出租(訪客可連)或閒置掛機進行中,都要維持 MapleStory 在前景:
            # 訪客/掛機的輸入都是硬體 HID,只會進「當前焦點視窗」。
            if remote_access.info()["active"] or idle_mode.is_running():
                await loop.run_in_executor(None, _tick)
        except Exception:
            pass
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app):
    global mouse
    for _line in cpu_tune.apply():      # 降低對遊戲的 CPU 干擾(見 cpu_tune 說明)
        print(f"[cpu] {_line}")
    _check_token_strength()
    if REMOTE_MODE:
        _print_remote_banner()
        tunnel.start(PORT)
    guard = asyncio.create_task(_rental_guard_loop())
    keyboard.start()
    idle_mode.set_keyboard(keyboard)
    # 閒置施放技能前的焦點確保:用統一焦點系統的輕量分支 enforce_focus
    # (只在失焦時切回、不重啟擷取管線),避免每次施放都觸發 restart。
    idle_mode.set_focus_fn(lambda: video_pipeline.enforce_focus(GUARD_EXE))
    calib.set_keyboard(keyboard)
    calib.set_focus_fn(lambda: video_pipeline.guard_focus(GUARD_EXE))
    navigator.set_keyboard(keyboard)
    navigator.set_focus_fn(lambda: video_pipeline.guard_focus(GUARD_EXE))
    navigator.set_terrain_fn(lambda: (mapdata.points(mapdata.current_map_id()),
                                      mapdata.platforms(mapdata.current_map_id())))
    # 職業:第一次啟動把現況存成「蓮」,之後每次啟動套用上次選的那個。
    # navigator 的移動參數是模組級變數,不套用就會退回寫死的預設(=蓮的值)。
    jobs.ensure_default()
    _cur_job = jobs.current_name()
    if _cur_job:
        _ok, _msg = jobs.apply(_cur_job)
        print(f"[job] 套用職業「{_cur_job}」: {_msg}")
    # 紫標(=符文 rune)出現 → 開了自動解除就去解,否則沿用「暫停巡邏」(危險規避)。
    # Telegram 通知照舊(在 minimap 內)。
    rune.set_hooks(navigator=navigator, keyboard=keyboard,
                   focus_fn=lambda: video_pipeline.guard_focus(GUARD_EXE),
                   resume_fn=_start_patrol_current)

    def _on_minimap_event(kind, data):
        if kind == "purple":
            if not rune.trigger_solve(data):      # 沒接手 → 回 False → 暫停巡邏
                navigator.pause_purple()
    minimap.set_event_hook(_on_minimap_event)

    # Arduino 上的紅色實體按鈕 → 強制停止巡邏(緊急煞車)。
    # 【為什麼要有實體的】中控頁的停止鍵要網路可達、頁面還活著才按得到;人就在機器
    # 前面卻得先開網頁的情況下,這顆按鈕是最短路徑。停止後順手放開所有按鍵 ——
    # 巡邏執行緒收尾本來就會放,但如果當下卡在別的狀態(例如解符文按著方向鍵),
    # 只 stop 不放開會讓角色一直往某個方向走。
    def _on_arduino_button(name):
        if name != "STOP":
            print(f"[Arduino] 未知的按鈕事件 {name},忽略")
            return
        was = navigator.status().get("running")
        navigator.stop(user=True)   # 實體按鈕就是「人要停」,解符文流程必須收手
        keyboard.release_all()
        print(f"[Arduino] 紅色按鈕 → {'已停止巡邏' if was else '本來就沒在跑,已放開所有按鍵'}")
    keyboard.set_button_hook(_on_arduino_button)

    # 死亡自動復活:巡邏每輪檢查有沒有跳出「確定要在當前地圖中復活嗎?」對話框
    revive.set_hooks(focus_fn=lambda: video_pipeline.guard_focus(GUARD_EXE))
    navigator.set_round_hook(revive.check)
    if not mouse.start():
        # 沒有 KMBox：優先降級到 Arduino HID 滑鼠(仍是硬體訊號，反作弊安全)；
        # 連 Arduino 都沒有時才退到軟體滑鼠(SendInput，可能被反作弊偵測)。
        if getattr(keyboard, "connected", False):
            from arduino import ArduinoMouse
            mouse = ArduinoMouse(keyboard)
        else:
            from soft_mouse import SoftMouse
            mouse = SoftMouse()
        mouse.start()
    video_pipeline.set_mouse(mouse)   # 供「切視窗時的標題列啟用點擊」使用
    revive.set_hooks(mouse=mouse)     # 要在降級決定完之後才注入(可能是 KMBox/Arduino/軟體)
    # 影像主力為 WebRTC(MediaMTX+ffmpeg)，由 /video/start 啟動 ffmpeg。
    yield
    guard.cancel()
    idle_mode.stop()
    calib.stop()
    minimap.watch_stop()
    import audio_pipeline
    audio_pipeline.stop()
    keyboard.close()
    mouse.close()
    video_pipeline.stop()
    screen.stop()
    tunnel.stop()
    rune.shutdown()
    wgc.stop()


app = FastAPI(title="maple 遠端遊玩", lifespan=lifespan)


# ===== 驗證：主人(主 token) vs 訪客(限時短密碼) =====
# 訪客安全模型（伺服器端強制，非前端裝飾）：
#   * 只能連 / (首頁)、/video (MJPEG)、/ws/input (輸入,白名單)、/remote/info (剩餘時間)
#   * WS 輸入白名單：僅鍵盤 4 / left / right；滑鼠/滾輪訊息一律丟棄
#   * 其他 API（畫面設定/視窗清單/對準/剪貼簿/狀態/密碼與隧道管理）僅主 token 可用
#   * 密碼到期或被撤銷：WS 看門狗立即斷線、MJPEG 串流中止
GUEST_KEYS = {"4", "left", "right"}


def _is_owner(token):
    # 恆定時間比較：== 會在第一個不同字元就返回,理論上可被計時側信道逐字猜測
    return hmac.compare_digest(str(token or ""), AUTH_TOKEN)


def _token_ok(token):
    """主 token 永遠有效；限時短密碼(訪客)有效期內也可登入。"""
    return _is_owner(token) or remote_access.is_valid(token)


def _check(token):
    if not _token_ok(token):
        raise HTTPException(status_code=403, detail="invalid token")


def _check_owner(token):
    if not _is_owner(token):
        raise HTTPException(status_code=403, detail="owner token required")


def _via_tunnel(headers):
    """請求是否來自 Cloudflare Tunnel。cloudflared 轉發必帶 CF 標頭，且 Host 為
    *.trycloudflare.com。LAN 使用者偽造這些標頭只會被「降權」成訪客，無法反向
    提權，故此判斷可安全用於強制降權/選頁。"""
    host = (headers.get("host") or "").split(":")[0]
    return ("cf-ray" in headers) or ("cf-connecting-ip" in headers) \
        or host.endswith(".trycloudflare.com")


def _trusted_ip(host):
    """來源 IP 是否屬於信任網段：本機/私網(10,172.16,192.168)/Tailscale CGNAT
    (100.64/10)…等非公網位址。公網 IP 一律不信任。
    'testclient' 是 ASGI 測試工具的固定值(無真實 socket)，視為信任。"""
    if host == "testclient":
        return True
    if not host:
        return False                       # 取不到來源 → 不信任(fail-closed)
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _untrusted(headers, client):
    """不信任來源 = 一律訪客降權：經 Cloudflare Tunnel(CF 標頭/trycloudflare)，
    或來源是公網 IP(路由器直接轉發 8000 的情況)。這讓「直接 port-forward」也
    只暴露訪客面——主人 API 只有在家 LAN/Tailscale 摸得到。"""
    return _via_tunnel(headers) or not _trusted_ip(client.host if client else None)


# 不信任來源(隧道/公網)可觸達的路徑白名單。就算主 token 外洩，從公網也
# 打不到任何主人 API（畫面/視窗/剪貼簿/密碼與隧道管理…全部 403）。
TUNNEL_ALLOWED_PATHS = {"/", "/guest", "/video", "/remote/info",
                        "/guest/quality", "/guest/focus", "/favicon.ico"}


# 安全標頭（訪客頁/隧道回應）：CSP 擋外部資源注入與資料外送、禁 iframe 嵌入。
# inline script/style 是頁面自帶的所以需要 'unsafe-inline'；重點在 default-src 'self'
# ——就算有人設法注入 <script src=外部>、<img src=外部> 也會被瀏覽器擋下。
_SEC_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self' ws: wss:; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def tunnel_guard(request: Request, call_next):
    if _untrusted(request.headers, request.client):
        if request.url.path not in TUNNEL_ALLOWED_PATHS:
            return JSONResponse({"detail": "not available from untrusted source"},
                                status_code=403)
        resp = await call_next(request)
        for k, v in _SEC_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp
    return await call_next(request)


# ===== 首頁：依來源分流（前端網頁完全分離,避免注入/竄改攻擊面） =====
# 經 Cloudflare Tunnel 進來 → 只給極簡訪客頁 guest.html（不含任何主人功能
# 的程式碼）；家裡 Tailscale/LAN → 完整主人頁 index.html。
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


# 新版前端(React,原始碼在 webapp/,建置產物在 web/dist/)。舊版單檔 index.html
# 保留在 /legacy —— 新版還沒補齊的功能可以先回去用,不必一次切死。
_DIST_ASSETS = os.path.join(WEB, "dist", "assets")
if os.path.isdir(_DIST_ASSETS):
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=_DIST_ASSETS), name="assets")


@app.get("/")
def index(request: Request):
    if _untrusted(request.headers, request.client):
        return FileResponse(os.path.join(WEB, "guest.html"), headers=_NO_CACHE)
    dist = os.path.join(WEB, "dist", "index.html")
    if os.path.exists(dist):
        return FileResponse(dist, headers=_NO_CACHE)
    return FileResponse(os.path.join(WEB, "index.html"), headers=_NO_CACHE)


@app.get("/legacy")
def index_legacy():
    """舊版單檔介面(2600 行 index.html)。新版缺的功能先用這個。"""
    return FileResponse(os.path.join(WEB, "index.html"), headers=_NO_CACHE)


@app.get("/guest")
def guest_page():
    """訪客頁直接入口（LAN 測試、或把此路徑分享給訪客用）。"""
    return FileResponse(os.path.join(WEB, "guest.html"),
                        headers={**_NO_CACHE, **_SEC_HEADERS})


# ===== 影像備援：MJPEG 串流（WebRTC 不可用時才用；延遲較高） =====
# 訪客併發計數：一組密碼開太多串流會塞爆家用上傳頻寬(DoS)，超過上限直接拒絕。
_guest_streams = 0


@app.get("/video")
def video(request: Request, token: str = Query("")):
    _check(token)
    # 比照 /ws/input：經隧道/公網來源即使帶主 token 也一律降權為訪客——套用
    # 視窗裁切、到期檢查、併發上限等全部守門。(主 token 經隧道 still_valid 為
    # 假 → 一格都拿不到,徹底防「主 token 外洩→公網取全桌面」。)
    guest = (not _is_owner(token)) or _untrusted(request.headers, request.client)
    if guest and _guest_streams >= GUEST_MAX_CONN:
        raise HTTPException(status_code=429, detail="too many guest streams")
    screen.ensure_started()

    async def gen():
        global _guest_streams
        if guest:
            if _guest_streams >= GUEST_MAX_CONN:   # 與上面的檢查間有空窗,再驗一次
                return
            _guest_streams += 1
        try:
            interval = 1.0 / max(1, TARGET_FPS)
            boundary = b"--frame\r\n"
            while True:
                # 訪客的密碼到期/被撤銷 → 立刻停止長連線串流（不能靠登入時那一次驗證）
                if guest and not remote_access.still_valid(token):
                    break
                # 訪客只能看「視窗模式的 MapleStory」：目標不成立(遊戲關閉/切成
                # 全螢幕來源)時一格都不給——嚴防把整個桌面外洩給訪客。
                if guest and not video_pipeline.target_window_valid(GUARD_EXE):
                    await asyncio.sleep(0.5)
                    continue
                frame = screen.latest(window_only=guest)
                if frame is not None:
                    yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(interval)
        finally:
            if guest:
                _guest_streams -= 1

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ===== 畫面設定：讀取 / 即時調整（WebRTC 管線：螢幕 / FPS / 位元率） =====
@app.get("/config/video")
def get_video_config(token: str = Query("")):
    _check_owner(token)
    cfg = video_pipeline.settings()
    try:
        cfg["monitor_count"] = screen.monitor_count()
    except Exception:
        cfg["monitor_count"] = 1
    return JSONResponse(cfg)


@app.post("/config/video")
async def set_video_config(request: Request, token: str = Query("")):
    _check_owner(token)
    body = await request.json()
    # apply 可能觸發 ffmpeg 重啟(阻塞數秒)→ 丟執行緒池,不阻塞事件迴圈。
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: video_pipeline.apply(
        source=body.get("source"), window=body.get("window"), hwnd=body.get("hwnd"),
        monitor=body.get("monitor"), fps=body.get("fps"), bitrate=body.get("bitrate"),
        scale=body.get("scale"), gray=body.get("gray"),
    ))
    return JSONResponse(result)


# ===== 視窗清單（供手機端挑選要擷取的視窗） =====
@app.get("/windows")
def list_windows(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"windows": video_pipeline.list_windows()})


# ===== 對準視窗【主遠端】：把主人選定的視窗帶到前景(統一焦點系統) =====
# 與訪客/閒置不同:主人可對準「任意選定的視窗」(state.hwnd),不限 MapleStory。
@app.post("/window/focus")
def window_focus(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"ok": video_pipeline.focus_target()})


# ===== 浮動客戶端的安裝包下載（見 server/client_pkg.py） =====
# 客戶端是裝在【操控端】的,但建置產物躺在遊戲那台的專案目錄裡 —— 沒有這條路,
# 使用者在手機或另一台電腦上打開網頁時根本拿不到它。
# 只有主人能下載:那是一支能操控這台機器的程式。
@app.get("/client/status")
def client_status(token: str = Query("")):
    _check_owner(token)
    import client_pkg
    return JSONResponse({"zips": client_pkg.status(),
                         "direct": client_pkg.direct_files()})


@app.post("/client/prepare")
def client_prepare(token: str = Query(""), name: str = Query("win")):
    _check_owner(token)
    import client_pkg
    return JSONResponse(client_pkg.prepare(name))


@app.get("/client/download")
def client_download(token: str = Query(""), name: str = Query(""),
                    file: str = Query("")):
    """`file=` 取單檔產物(.AppImage/.deb/.dmg,使用者在該平台建好丟進 client/dist/)，
    `name=` 取壓縮好的 zip(win-unpacked 那種資料夾型產物)。"""
    _check_owner(token)
    import client_pkg
    if file:
        p = client_pkg.direct_path(file)     # 只接受掃出來的完全相同檔名，擋路徑穿越
        if p is None:
            raise HTTPException(status_code=404, detail="找不到這個產物")
        return FileResponse(p, media_type="application/octet-stream",
                            filename=os.path.basename(p))
    p = client_pkg.file_of(name or "win")
    if p is None:
        raise HTTPException(status_code=404, detail="尚未壓縮完成（先呼叫 /client/prepare）")
    return FileResponse(p, media_type="application/zip",
                        filename=client_pkg.filename_of(name or "win"))


# ===== 影像啟動 / 停止（注視畫面時由前端呼叫） =====
@app.post("/video/start")
def video_start(token: str = Query("")):
    _check_owner(token)
    ok = video_pipeline.ensure_running()
    import audio_pipeline
    audio_pipeline.start()          # 獨立音訊管線;失敗不影響影像
    return JSONResponse({"ok": ok})


@app.post("/video/stop")
def video_stop(token: str = Query("")):
    _check_owner(token)
    video_pipeline.stop()
    import audio_pipeline
    audio_pipeline.stop()
    return JSONResponse({"ok": True})


# ===== 剪貼簿：把操作端文字複製到被控端(主機) =====
@app.post("/clipboard")
async def set_clip(request: Request, token: str = Query("")):
    _check_owner(token)
    body = await request.json()
    return JSONResponse({"ok": clipboard.set_clipboard(body.get("text", ""))})


# ===== 狀態（硬體連線診斷） =====
@app.get("/status")
def status(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({
        "keyboard": {"arduino": getattr(keyboard, "connected", False)},
        "mouse": {"connected": getattr(mouse, "connected", False),
                  "software": getattr(mouse, "software", False)},
        "video": video_pipeline.settings(),
    })


# ===== 閒置(掛機)模式：僅中控(主人),與訪客模式互斥 =====
@app.get("/idle/status")
def idle_status(token: str = Query("")):
    _check_owner(token)
    s = idle_mode.status()
    # 焦點診斷:前景是否真的是 MapleStory。若否,技能/字母(WM_KEYDOWN)送不進去,
    # 只有方向鍵(GetAsyncKeyState 全域狀態)有效——中控頁據此提示使用者。
    s["target_foreground"] = video_pipeline.is_target_foreground(GUARD_EXE)
    return JSONResponse(s)


@app.post("/idle/start")
def idle_start(token: str = Query(""), duration: float = Query(0)):
    """開啟閒置掛機。duration=總啟用秒數(0=無限),到時自動關閉。"""
    _check_owner(token)
    # 與訪客模式互斥：出租(短密碼有效)期間不得掛機——訪客與掛機會搶同一套鍵盤
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409,
                            detail="訪客(出租)進行中，請先撤銷密碼/停止出租再開閒置模式")
    if calib.is_running():
        raise HTTPException(status_code=409, detail="運動校準進行中，請先等它結束/停止")
    # 閒置模式=純隨機 AFK(隨機移動＋放置輪迴),不導航、不需小地圖/座標。
    # (座標僅『自動巡邏』需要;紫標背景監看仍會啟動,但只做通知、不阻擋。)
    # 開場即統一焦點守衛一次(鎖定 MapleStory + 切到前景)+ 啟動擷取,
    # 讓中控頁監視畫面(/monitor/frame)有內容、掛機一開始就對準遊戲。
    video_pipeline.guard_focus(GUARD_EXE)
    screen.ensure_started()
    idle_mode.start(max(0.0, min(86400.0, duration)))   # 上限 24 小時
    minimap.watch_acquire("idle")   # 掛機期間背景監看小地圖(紫標 → Telegram 通知)
    return JSONResponse(idle_mode.status())


# ===== 地圖座標管理(掛機自動導航的前置;每張地圖獨立,僅主人) =====
@app.get("/map/status")
def map_status(token: str = Query("")):
    """當前地圖狀態:map_id(小地圖尺寸)、有無座標、巡邏點數與清單。"""
    _check_owner(token)
    return JSONResponse(mapdata.status())


@app.post("/map/record")
def map_record(token: str = Query("")):
    """把角色當前黃點位置記錄成一個巡邏點(容差去重)。"""
    _check_owner(token)
    mid = mapdata.current_map_id()
    if not mid:
        raise HTTPException(status_code=400, detail="偵測不到小地圖(先開啟小地圖偵測預覽)")
    s = minimap.status()
    d = s.get("dot")
    if not d or s.get("dot_stale"):
        raise HTTPException(status_code=400, detail="抓不到角色黃點,無法記錄")
    ok, n = mapdata.add_point(mid, d["x"], d["y"])
    return JSONResponse({"added": ok, **mapdata.status()})


@app.post("/map/remove_last")
def map_remove_last(token: str = Query("")):
    _check_owner(token)
    mapdata.remove_last(mapdata.current_map_id())
    return JSONResponse(mapdata.status())


@app.post("/map/clear")
def map_clear(token: str = Query("")):
    _check_owner(token)
    mapdata.clear(mapdata.current_map_id())
    return JSONResponse(mapdata.status())


@app.post("/map/name")
def map_name(token: str = Query(""), name: str = Query("")):
    _check_owner(token)
    mid = mapdata.current_map_id()
    if mid:
        mapdata.set_name(mid, name)
    return JSONResponse(mapdata.status())


# ===== 具名設置(profile):保存/讀取整套「記錄點+放置技能+平A」 =====
@app.post("/map/profile/save")
def map_profile_save(token: str = Query(""), name: str = Query("")):
    """把當前地圖的記錄點(含放置技能/精確)+平A,存成具名設置。"""
    _check_owner(token)
    ok, msg = mapdata.save_profile(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return JSONResponse({"ok": True, "name": msg, "profiles": mapdata.list_profiles()})


@app.get("/map/profile/list")
def map_profile_list(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"profiles": mapdata.list_profiles()})


@app.post("/map/profile/load")
def map_profile_load(token: str = Query(""), name: str = Query("")):
    """載入具名設置 → 覆蓋當前地圖的記錄點(含放置技能),並套用平A。"""
    _check_owner(token)
    ok, msg = mapdata.load_profile(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return JSONResponse({"ok": True, "loaded": msg, **mapdata.status()})


@app.post("/map/profile/delete")
def map_profile_delete(token: str = Query(""), name: str = Query("")):
    _check_owner(token)
    mapdata.delete_profile(name)
    return JSONResponse({"profiles": mapdata.list_profiles()})


@app.post("/map/attack")
def map_attack(token: str = Query(""), key: str = Query("a"), mode: str = Query("hold2s"),
               jump_atk: bool = Query(False), fall_atk: bool = Query(False)):
    """設定平A(普通攻擊)鍵與施放方式:mode=hold2s(長按2秒) / tap2(按兩次)。全域共用。"""
    _check_owner(token)
    mapdata.set_attack(key, mode, jump_atk, fall_atk)
    return JSONResponse(mapdata.status())


@app.post("/map/point_skill")
def map_point_skill(token: str = Query(""), index: int = Query(...),
                    skill: str = Query(""), cd: float = Query(0.0),
                    precise: bool = Query(False), skip: bool = Query(False),
                    face: str = Query("")):
    """設第 index 個巡邏點的『放置技能』鍵與冷卻秒數(空鍵=取消)。僅到點施放、冷卻中略過。
    face='left'/'right':施放前再按一次該方向鍵確保面向;''=不限。"""
    _check_owner(token)
    mid = mapdata.current_map_id()
    if not mid:
        raise HTTPException(status_code=400, detail="偵測不到小地圖")
    mapdata.set_point_skill(mid, index, skill, cd, precise, skip, face)
    return JSONResponse(mapdata.status())


# ===== 地形:平台(可走線段) + 繩索(層間電梯),供跨層路徑規劃 =====
@app.post("/map/platform/add")
def map_platform_add(token: str = Query(""), y: int = Query(...),
                     xa: int = Query(...), xb: int = Query(...)):
    """新增平台:A端x=xa、B端x=xb、所在層 y(前端讀黃點得出)。"""
    _check_owner(token)
    mid = mapdata.current_map_id()
    if not mid:
        raise HTTPException(status_code=400, detail="偵測不到小地圖")
    mapdata.add_platform(mid, y, xa, xb)
    return JSONResponse(mapdata.status())


@app.post("/map/platform/remove")
def map_platform_remove(token: str = Query(""), index: int = Query(...)):
    _check_owner(token)
    mapdata.remove_platform(mapdata.current_map_id(), index)
    return JSONResponse(mapdata.status())


@app.post("/map/rope/add")
def map_rope_add(token: str = Query(""), x: int = Query(...)):
    """新增繩索(只記 x=當前黃點;覆蓋哪些層由平台幾何推斷)。"""
    _check_owner(token)
    mid = mapdata.current_map_id()
    if not mid:
        raise HTTPException(status_code=400, detail="偵測不到小地圖")
    mapdata.add_rope(mid, x)
    return JSONResponse(mapdata.status())


@app.post("/map/rope/remove")
def map_rope_remove(token: str = Query(""), index: int = Query(...)):
    _check_owner(token)
    mapdata.remove_rope(mapdata.current_map_id(), index)
    return JSONResponse(mapdata.status())


# ===== 自動導航(掛機用):背景執行緒走到目標座標,狀態可查 =====
@app.post("/nav/move_to")
def nav_move_to(token: str = Query(""), x: int = Query(...), y: int = Query(...)):
    """啟動背景導航到小地圖座標 (x,y)。與訪客/校準互斥。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中,不可導航")
    if calib.is_running():
        raise HTTPException(status_code=409, detail="運動校準進行中")
    screen.ensure_started()
    navigator.clear_user_stop()   # 使用者重新發起動作 → 撤掉先前的停止要求
    ok, msg = navigator.move_to(x, y)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return JSONResponse(navigator.status())


def _start_patrol_current():
    """依當前地圖啟動巡邏(nav_patrol 與 rune 解完續巡邏共用)。回 (ok,msg)。
    傳函式而非快照:巡邏每輪重讀最新點 → 運行中改放置技能/冷卻即時生效(不必停巡邏)。"""
    mid = mapdata.current_map_id()
    if not mapdata.has_layout(mid):
        return False, "此地圖尚未設定座標"
    # 場上已經有符文就先去解:紫標 hook 是邊緣觸發、又被 Telegram 通知冷卻節流,
    # 「開巡邏前符文就在」不會觸發它,而巡邏兜底看到紫標就停 → 開了巡邏卻原地不動。
    # 解完 rune 會自己呼叫這個函式接回巡邏。【解不掉而放棄時也會接回】—— 但那個符文
    # 已經進了 rune 的放棄冷卻,下面這行會直接回 False,所以不會遞迴。
    if rune.solve_if_present():
        return True, "偵測到符文,先去解除,解完自動續巡邏"
    att = mapdata.get_attack()
    navigator.set_jump_hold_atk(att.get("jump_atk", False))
    navigator.set_fall_hold_atk(att.get("fall_atk", False))
    # 這裡刻意【不】呼叫 screen.ensure_started():那是 MJPEG 預覽的 JPEG 編碼管線,
    # 巡邏用不到(導航讀的是 wgc 共用影格)。開著等於沒人看還每秒編 30 張 JPEG 搶 CPU。
    # 中控頁的 /monitor/frame 會自己惰性啟動它。
    video_pipeline.guard_focus(GUARD_EXE)
    return navigator.patrol_start(lambda: mapdata.points(mid), att["key"], att["mode"])


@app.get("/nav/patrol_minutes")
def nav_patrol_minutes_get(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"minutes": mapdata.get_patrol_minutes()})


@app.post("/nav/patrol_minutes")
def nav_patrol_minutes_set(token: str = Query(""), minutes: int = Query(...)):
    """設定巡邏時限(分鐘),0=無限。存檔,下次開巡邏沿用。"""
    _check_owner(token)
    return JSONResponse({"minutes": mapdata.set_patrol_minutes(minutes)})


@app.post("/nav/patrol")
def nav_patrol(token: str = Query(""), minutes: int = Query(-1)):
    """啟動自動巡邏掛機:巡邏點【隨機不連號】走位 + 到點平A + 放置技能(含冷卻)。
    平A鍵/施放方式取自中控『平A設定』;放置技能取自各點設定。
    minutes:巡邏時限,0=無限,-1(預設)=沿用存檔設定;有給值時同時存檔。
    需先設定座標(has_layout);與訪客/校準/閒置互斥。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中,不可巡邏")
    if calib.is_running() or idle_mode.is_running():
        raise HTTPException(status_code=409, detail="校準/閒置模式進行中,請先停止")
    # 先存時限再檢查地圖:否則地圖偵測不到(map_id 空)時直接 409,使用者輸入的分鐘數
    # 會被一起吞掉,下次還得重設一次。
    mins = mapdata.set_patrol_minutes(minutes) if minutes >= 0 \
        else mapdata.get_patrol_minutes()
    mid = mapdata.current_map_id()
    if not mid:
        raise HTTPException(status_code=409,
                            detail="偵測不到小地圖(請確認遊戲畫面/小地圖沒被收起)")
    if not mapdata.has_layout(mid):
        raise HTTPException(status_code=409,
                            detail="此地圖尚未設定座標,請先在中控記錄巡邏點")
    # 時限只在【使用者按下開始巡邏】時設定一次。解符文完成後會再呼叫 _start_patrol_current
    # 接回巡邏,那條路徑刻意不重設期限,否則每解一次符文就把掛機時間往後延。
    navigator.set_patrol_deadline(mins * 60)
    # 使用者重新按下開始巡邏 → 撤掉先前的停止要求。刻意放在這裡而不是
    # _start_patrol_current:那個函式解符文接回巡邏時也會走,清在那裡等於讓自動流程
    # 有機會抹掉人按下的停止。
    navigator.clear_user_stop()
    ok, msg = _start_patrol_current()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return JSONResponse(navigator.status())


@app.get("/nav/status")
def nav_status(token: str = Query("")):
    _check_owner(token)
    s = navigator.status()
    # 併入單一狀態供中控頁顯示:解符文期間 navigator 也在跑導航,只看 nav.running
    # 會誤顯示成「巡邏中」。
    s["overall"] = rune.overall_state()
    s["rune_enabled"] = rune.status()["enabled"]
    # 各開關現值一併回傳,讓中控頁的狀態面板不必再多打幾支 API 才能顯示徽章
    att = mapdata.get_attack()
    s["attack_key"] = att.get("key", "")
    s["attack_mode"] = att.get("mode", "")
    s["jump_atk"] = bool(att.get("jump_atk", False))
    s["fall_atk"] = bool(att.get("fall_atk", False))
    return JSONResponse(s)


@app.post("/nav/stop")
def nav_stop(token: str = Query("")):
    """使用者手動停止 → 連時限一起清掉(倒數不該繼續跑)。
    注意不能把清除放進 navigator.stop():解符文流程一開始就會呼叫它,清掉的話
    解完接回巡邏會變成無限巡邏,設定的時限等於失效。"""
    _check_owner(token)
    navigator.stop(user=True)      # user=True:解符文流程看到這支旗子才會收手
    navigator.set_patrol_deadline(None)
    return JSONResponse(navigator.status())


# ===== 符文(rune)自動解除:常駐 claude CLI 辨識箭頭方向 =====
@app.get("/rune/status")
def rune_status(token: str = Query("")):
    _check_owner(token)
    return JSONResponse(rune.status())


@app.post("/rune/enable")
def rune_enable(token: str = Query(""), on: int = Query(...)):
    """開/關符文自動解除。關閉時紫標維持原本『暫停巡邏 + Telegram 通知』行為。"""
    _check_owner(token)
    rune.set_enabled(bool(on))
    return JSONResponse(rune.status())


@app.post("/rune/line")
def rune_line(token: str = Query(""), cv: int = Query(None), claude: int = Query(None)):
    """獨立開關兩條辨識線路(1 線 CV / 2 線 claude)。兩條都開 = 1 線先跑、讀不到才退 2 線。
    只帶其中一個參數就只改那條。兩條都關會被擋下(回 ok:false)。"""
    _check_owner(token)
    ok, msg = rune.set_lines(cv=cv, claude=claude)
    return JSONResponse({"ok": ok, "msg": msg, **rune.status()})


@app.post("/rune/collect")
def rune_collect_once(token: str = Query(""), i: int = Query(0)):
    """採集一筆訓練樣本:按一次 B → 連拍 → 存 band/full/meta(旋轉時另存序列)。

    【只按 B,不按方向鍵、不導航】—— 使用者要求角色留在符文上不移動。
    由外部迴圈以固定間隔呼叫(建議 15 秒,配合 10~14 秒的謎題窗)。
    """
    _check_owner(token)
    import rune_collect
    return JSONResponse(rune_collect.capture_once(i))


@app.post("/fiona/start")
def fiona_start(token: str = Query(""), save_bands: int = Query(1)):
    """啟動菲歐娜解謎的【觀察模式】—— 只記錄「我會選哪個」,不點擊、不按鍵。

    為什麼先不接點擊:單輪正確率目前 11/12(91.7%),12 輪的 95% 信賴區間是
    [64.6%, 98.5%],寬到無法判斷能不能用;而一場 4 輪錯一輪就受懲罰。先累積
    對照資料(真值來自遊戲自己畫的計分格),數字夠了再談下注。

    save_bands=0 可省磁碟,但之後就無法拿原始資料重跑改進後的追蹤器。
    """
    _check_owner(token)
    import fiona_live
    return JSONResponse(fiona_live.start(save_bands=bool(save_bands)))


@app.post("/fiona/stop")
def fiona_stop(token: str = Query("")):
    """停止觀察。已累積的資料留著,不會清掉。"""
    _check_owner(token)
    import fiona_live
    return JSONResponse(fiona_live.stop())


@app.get("/fiona/status")
def fiona_status(token: str = Query("")):
    """即時狀態 + 本次啟動後的統計 + 最近幾輪的預測/真值對照。"""
    _check_owner(token)
    import fiona_live
    s = fiona_live.status()
    s["summary"] = fiona_live.summary()      # 跨啟停的累計(掃採集目錄)
    return JSONResponse(s)


@app.post("/rune/warmup")
def rune_warmup(token: str = Query("")):
    """預熱 claude worker(第一次 ~6s,之後每次辨識降到 2.5~4.8s)。"""
    _check_owner(token)
    ok, msg = rune.worker_warmup()
    return JSONResponse({"ok": ok, "msg": msg, "worker": rune.status()["worker"]})


@app.post("/rune/detect")
def rune_detect(token: str = Query("")):
    """乾跑辨識當前畫面(不碰角色)+ 存 server/rune_shot.png,供校準裁切框。"""
    _check_owner(token)
    return JSONResponse(rune.detect_now())


@app.get("/rune/dataset")
def rune_dataset(token: str = Query("")):
    """自動標註資料集現況(筆數、各線路來源分佈)。"""
    _check_owner(token)
    return JSONResponse(rune.dataset_status())


@app.post("/rune/dataset/eval")
def rune_dataset_eval(token: str = Query("")):
    """拿資料集重跑 1 線,量它在真實案例上的正確率。
    2 線救回來的樣本正是 1 線最該補強的地方,所以這個數字才是調參的依據。"""
    _check_owner(token)
    return JSONResponse(rune.dataset_eval())


@app.get("/rune/capsule")
def rune_capsule(token: str = Query(""), scale: int = Query(3)):
    """1 線(CV)的膠囊預覽 JPEG:框出膠囊、四等分線、每格判到的方向。
    沒開謎題時回整幀縮圖 + no capsule 字樣(不回 503,否則前端分不出
    「沒開謎題」與「定位壞了」)。前端可定時輪詢當即時預覽。"""
    _check_owner(token)
    jpg = rune.capsule_preview_jpeg(scale=max(1, min(6, scale)))
    if jpg is None:
        raise HTTPException(status_code=503,
                            detail="拿不到 MapleStory 視窗影格(遊戲開著嗎?)")
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/rune/live")
def rune_live(token: str = Query(""), scale: int = Query(1)):
    """即時偵測預覽 JPEG:抓當前遊戲畫面 + 一小段連拍(~0.5 秒,見
    `rune_live_state.BURST_SECS`),跑一次現行主路徑(RT-DETR 偵測箭頭 + 幾何
    選擇),畫出所有候選框+信心分數(灰)、幾何選擇挑中的 4 支(即時預覽沒有
    真值可比對錯,固定一色),取代已過時的 /rune/capsule(那是為除錯已被取代
    的 1 線 find_capsule 做的,1 線現在只是模型不可用時的退路)。

    最上層大字結果條是【跨呼叫累積判定】(見 `rune_live_state.py`),不是單幀
    瞬間讀值:旋轉款符文(解放輪)單幀讀到的只是箭頭那一瞬間剛好指哪,不是
    答案,答案是箭頭晃動(角速度反轉)的方向,要靠使用者連續打開預覽、伺服器
    跨多次呼叫累積觀測才判得出來。靜止的箭頭第一次呼叫就可能定案;旋轉的箭頭
    定案前顯示「觀察中」與目前角度,約 1~3 秒(連續呼叫數次)後補上答案。
    每支各自獨立判斷(同一顆符文可能同時有幾支靜止、幾支在轉)。

    模型未載入時不失敗,改標明並顯示 1 線退路的內容(這種情況判不了旋轉,
    也會重置累積緩衝)。拿不到遊戲畫面時回帶說明文字的圖,不是 503(前端要
    能分辨「遊戲沒開」與「偵測壞了」),同樣會重置累積緩衝。累積緩衝的詳細
    JSON 見 GET /rune/live/info。"""
    _check_owner(token)
    jpg = rune_viz.render_live_jpeg(scale=max(1, min(6, scale)))
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/rune/live/info")
def rune_live_info(token: str = Query("")):
    """`/rune/live` 最近一次呼叫的累積判定狀態(JSON),給前端顯示每支的文字
    細節用。【不觸發新的擷取】——直接讀 `rune_live_state` 在上一次 /rune/live
    呼叫時已經算好、快取起來的結果,避免前端每次輪詢都多打一次全速連拍
    (那樣會讓兩個端點對不上、也白白多耗一次擷取成本)。前端應該先打
    /rune/live 拿圖,再打這支拿文字細節。從未打過 /rune/live 時回
    reason="not_started" 的預設值。"""
    _check_owner(token)
    import rune_live_state
    return JSONResponse(rune_live_state.get_last_status())


# ===== 符文箭頭偵測測試器(離線資料集,不碰遊戲/角色)=====
@app.get("/rune/viz")
def rune_viz_img(token: str = Query(""), src: str = Query("real"), i: int = Query(0)):
    """對指定資料集樣本跑一次偵測,畫上候選框+信心分數(灰)、真值框(藍)、
    幾何選擇挑中的 4 支+判向對錯(綠/紅)。回 JPEG。i 超出範圍會取模,方便
    前端一路按「下一張」。首次呼叫要載入模型,可能要 0.5~3 秒。"""
    _check_owner(token)
    try:
        fn, frame, gt_boxes, idx, total = rune_viz.sample(src, i)
    except rune_viz.SourceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        jpg = rune_viz.render_jpeg(frame, gt_boxes, fn, idx, total)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/rune/viz/info")
def rune_viz_info(token: str = Query(""), src: str = Query("real"), i: int = Query(0)):
    """跟 /rune/viz 同一張樣本的結構化版本:候選數、選出幾支、判向對幾支、
    每支的預測方向/真值/角度/is_settled、推論耗時。"""
    _check_owner(token)
    try:
        fn, frame, gt_boxes, idx, total = rune_viz.sample(src, i)
    except rune_viz.SourceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    info = rune_viz.compute_info(frame, gt_boxes)
    return JSONResponse({"src": src, "file": fn, "index": idx, "total": total, **info})


@app.get("/rune/viz/stats")
def rune_viz_stats(token: str = Query("")):
    """各來源離線量測的參考基準(端到端單支/四支全對正確率)。寫死,不即時重算
    ——重算 335 張要好幾分鐘,不適合放在網頁請求裡。"""
    _check_owner(token)
    return JSONResponse(rune_viz.STATS)


# ===== 死亡自動復活 =====
@app.get("/revive/status")
def revive_status(token: str = Query("")):
    _check_owner(token)
    return JSONResponse(revive.status())


@app.post("/revive/enable")
def revive_enable(token: str = Query(""), on: int = Query(...)):
    _check_owner(token)
    revive.set_enabled(bool(on))
    return JSONResponse(revive.status())


@app.post("/revive/now")
def revive_now(token: str = Query("")):
    """手動觸發一次復活檢查(偵測到就【真的點擊】)。供測試用;正式運作是巡邏每輪自動呼叫。
    需連續 CONFIRM_HITS 次偵測到才會點,所以第一次呼叫通常只累積次數。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中")
    handled = revive.check()
    return JSONResponse({"handled": handled, **revive.status()})


@app.post("/revive/detect")
def revive_detect(token: str = Query("")):
    """乾跑偵測「確定要在當前地圖中復活嗎?」對話框,不點滑鼠。
    會存 revive_shot.png(找到時把確認鈕框起來),供校準顏色/尺寸門檻。"""
    _check_owner(token)
    return JSONResponse(revive.detect())


@app.get("/rune/probe")
def rune_probe(token: str = Query("")):
    """只讀不動:角色點與紫標的小地圖座標、距離、紫標是否被角色蓋住。"""
    _check_owner(token)
    return JSONResponse(rune.probe())


@app.post("/rune/solve")
def rune_solve(token: str = Query(""), resume: int = Query(0)):
    """手動觸發完整解除流程(導航→開謎題→辨識→按方向→驗證)。背景執行,用 /rune/status 看進度。
    resume=1 才在解完後接回巡邏;測試時預設 0,角色留在原地。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中")
    return JSONResponse(rune.solve_now(bool(resume)))


@app.post("/rune/calib")
def rune_calib(token: str = Query(""), dxs: str = Query("0,3,6,9,12,15"),
               cooldown: float = Query(15.0)):
    """量測符文互動半徑:逐一走到紫標旁 dx 格按啟動鍵,看謎題開不開(不按方向鍵)。
    每格之間等 cooldown 秒讓謎題自然關閉。耗時約 len(dxs) x (走路+cooldown)。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中")
    try:
        vals = tuple(int(v) for v in dxs.split(",") if v.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="dxs 格式錯誤,例:0,3,6,9")
    return JSONResponse(rune.calibrate(vals, cooldown))


@app.post("/rune/test")
def rune_test(token: str = Query(""), solve: int = Query(0)):
    """測試:角色須自己站在符文上 → 焦點 + 按啟動鍵 → 辨識。solve=1 才真的按方向鍵。"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="訪客(出租)進行中")
    return JSONResponse(rune.test_activate(bool(solve)))


# ===== 中控頁監視畫面：最新單張 JPEG(主人專用,前端每秒抓一張 ≈1 秒延遲) =====
@app.get("/monitor/frame")
def monitor_frame(token: str = Query("")):
    """回傳最新一張擷取影格(視窗模式=MapleStory 視窗裁切)。單張、低頻抓取,
    用於儀表板監視掛機狀態——省頻寬、約 1 秒延遲但穩定流暢。僅主人可用。"""
    _check_owner(token)
    screen.ensure_started()
    frame = screen.latest()
    if frame is None:
        raise HTTPException(status_code=503, detail="尚無影格")
    return Response(content=frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# ===== 轉向控制 + 方向預覽：以「最後觸發的方向鍵」為當前面向 =====
# 面向直接讀 keyboard.last_dir——任何來源(WS 輸入/校準/掛機/轉向)按下 left/right
# 都會更新它,所以永遠反映「最後一次觸發的方向」。二段跳/移動一律朝此方向前進。
@app.get("/face")
def get_face(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"dir": getattr(keyboard, "last_dir", "right")})


@app.post("/face/{d}")
def set_face(d: str, token: str = Query("")):
    """轉向:key_down→按住 60ms→key_up。用 press 而非 tap,確保遊戲讀到轉向
    (tap 放開太快會漏讀 → 轉向失敗)。keyboard 會把 last_dir 更新成這個方向。"""
    _check_owner(token)
    if d not in ("left", "right"):
        raise HTTPException(status_code=400, detail="dir 必須是 left/right")
    keyboard.key_down(d)
    time.sleep(0.06)
    keyboard.key_up(d)
    print(f"[face] 轉向 {d}")
    return JSONResponse({"dir": getattr(keyboard, "last_dir", "right")})


# ===== 掛機(自動)模式基礎：小地圖偵測(自適應大小)+遠端預覽,僅主人 =====
@app.get("/minimap/frame")
def minimap_frame(token: str = Query(""), view: str = Query("annot")):
    """偵測 MapleStory 左上角小地圖並回標註後 JPEG(前端每秒抓一張)。
    view=annot 整視窗標註縮圖 / crop 小地圖裁切放大。只取 maplestory.exe 視窗。"""
    _check_owner(token)
    jpg = minimap.debug_jpeg(view="crop" if view == "crop" else "annot")
    if jpg is None:
        raise HTTPException(status_code=503,
                            detail="拿不到 MapleStory 視窗影格(遊戲開著嗎?)")
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/minimap/status")
def minimap_status(token: str = Query("")):
    """最近一次小地圖偵測結果(由 /minimap/frame 驅動,不另抓影格)。"""
    _check_owner(token)
    return JSONResponse(minimap.status())


@app.post("/minimap/watch")
def minimap_watch(token: str = Query(""), on: int = Query(1), ttl: float = Query(30.0)):
    """中控頁宣告「我需要背景偵測」(開著巡邏分頁時)。

    這是「偵測不依賴預覽」的關鍵:小地圖偵測原本只由預覽輪詢或巡邏迴圈驅動,兩者都沒跑
    時伺服器就不知道現在在哪張地圖 —— 中控頁的巡邏點/地形/平A 全部顯示不出來,而且
    「開始巡航」也會因為 current_map_id() 拿不到地圖而失敗。改由前端在進入巡邏分頁時
    宣告需求,伺服器就每 2 秒自己偵測一次,狀態端點只讀快取、不必阻塞去抓幀。

    ttl:必須有 —— 瀏覽器直接關掉不會有人來撤銷,沒有存活時間就會留一條每 2 秒抓幀的
    執行緒永遠跑。前端定期續約即可。"""
    _check_owner(token)
    if on:
        minimap.watch_acquire("ui", ttl=max(5.0, min(300.0, ttl)))
    else:
        minimap.watch_release("ui")
    return JSONResponse(minimap.watch_status())


@app.post("/minimap/redetect")
def minimap_redetect(token: str = Query("")):
    """手動解除小地圖鎖定,下一幀重新偵測(換地圖/誤鎖時用)。"""
    _check_owner(token)
    minimap.redetect()
    return JSONResponse(minimap.status())


# ===== 職業設定:攻擊 + 移動參數的具名組合 =====
@app.get("/job/status")
def job_status(token: str = Query("")):
    """{current, jobs:[名稱], detail:{name,move,attack}, defaults}"""
    _check_owner(token)
    return JSONResponse(jobs.status())


@app.post("/job/save")
async def job_save(request: Request, token: str = Query("")):
    """body: {"name":"漂移","move":{...},"attack":{...}}
    move/attack 省略時沿用該職業既有值,所以可以只改其中一半。"""
    _check_owner(token)
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少職業名稱")
    rec = jobs.save(name, body.get("move"), body.get("attack"), body.get("skills"))
    if rec is None:
        raise HTTPException(status_code=400, detail="職業名稱無效")
    # 存的就是當前選中的那個 → 立刻生效,不必再按一次套用
    if jobs.current_name() == rec["name"]:
        jobs.apply(rec["name"])
    return JSONResponse(rec)


@app.post("/job/apply")
def job_apply(token: str = Query(""), name: str = Query(...)):
    """切換職業:攻擊設定寫回 _attack.json,移動參數注入 navigator。"""
    _check_owner(token)
    if navigator.is_running():
        raise HTTPException(status_code=409, detail="巡邏/導航進行中,請先停止再換職業")
    ok, msg = jobs.apply(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return JSONResponse(jobs.status())


@app.post("/job/delete")
def job_delete(token: str = Query(""), name: str = Query(...)):
    _check_owner(token)
    if name == jobs.current_name():
        raise HTTPException(status_code=400, detail="不能刪除目前使用中的職業")
    if not jobs.delete(name):
        raise HTTPException(status_code=404, detail="找不到該職業")
    return JSONResponse(jobs.status())


# ===== EXP 進度 =====
@app.get("/exp/status")
def exp_status(token: str = Query("")):
    """讀畫面最下方經驗條上的文字 → {ok, exp, pct, text, err, gained, rate}。
    每次呼叫抓一幀現讀(實測 0.9ms),不做背景輪詢。"""
    _check_owner(token)
    return JSONResponse(exp.status())


@app.post("/exp/reset")
def exp_reset(token: str = Query("")):
    """把「本次累計」的基準點歸零到目前經驗值。"""
    _check_owner(token)
    return JSONResponse(exp.reset())


# ===== 運動校準(掛機模式·軌跡學習):量測輸入→位移,僅主人、與掛機/訪客互斥 =====
@app.post("/calib/start")
async def calib_start(request: Request, token: str = Query("")):
    """body: {"kind":"move|jump","values":[...],"direction":"right|left|alt|"}"""
    _check_owner(token)
    if remote_access.info()["active"]:
        raise HTTPException(status_code=409, detail="出租進行中,不可校準")
    if idle_mode.is_running():
        raise HTTPException(status_code=409, detail="閒置掛機進行中,請先關閉")
    body = await request.json()
    ok, msg = calib.start(body.get("kind", ""), body.get("values") or [],
                          body.get("direction", ""), body.get("skill_key", "x"))
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return JSONResponse(calib.status())


@app.get("/calib/status")
def calib_status(token: str = Query("")):
    _check_owner(token)
    return JSONResponse(calib.status())


@app.post("/calib/stop")
def calib_stop(token: str = Query("")):
    _check_owner(token)
    calib.stop()
    return JSONResponse(calib.status())


@app.post("/idle/stop")
def idle_stop(token: str = Query("")):
    _check_owner(token)
    idle_mode.stop()
    minimap.watch_release("idle")   # 只撤掉掛機這一方;中控頁若還開著巡邏分頁,偵測要繼續
    return JSONResponse(idle_mode.status())


# ===== Arduino 韌體:寫入代碼 + 測試訊號 =====
@app.get("/arduino/status")
def arduino_status(token: str = Query("")):
    """序列埠清單、猜到的板子、內嵌韌體資訊、能不能燒錄。"""
    _check_owner(token)
    ok, why = firmware.available()
    return JSONResponse({
        "connected": bool(getattr(keyboard, "connected", False)),
        "port": getattr(keyboard, "port", ""),
        # 實體紅色按鈕:按過幾次、最後一次何時。按了沒反應時用這個分辨是
        # 「板子沒送上來(接線/韌體)」還是「送上來了但停止沒生效」。
        "button_n": getattr(keyboard, "btn_n", 0),
        "button_last": getattr(keyboard, "btn_last", None),
        "ports": firmware.list_ports(),
        "guess": firmware.guess_port(),
        "firmware": firmware.firmware_info(),
        "can_flash": ok, "why": why,
        "test_keys": sorted(firmware.TEST_TOKENS),
    })


@app.post("/arduino/flash")
def arduino_flash(token: str = Query(""), port: str = Query("")):
    """把 exe 內嵌的韌體 .hex 燒進板子。

    燒錄期間必須放掉主程式對序列埠的佔用(1200 baud touch 開不了被佔用的埠),
    所以會先 keyboard.close();燒完再 start() 接回來。這段時間鍵盤輸入會中斷,
    屬預期 —— 燒錄本身就會讓板子重開機。"""
    _check_owner(token)
    r = firmware.flash(port=port.strip(), release_fn=keyboard.close)
    time.sleep(1.5)                  # 等板子燒完重新列舉成應用程式位址
    try:
        keyboard.start()             # 重新連線(埠號可能變了,start 會自動偵測)
        r["reconnected"] = bool(keyboard.connected)
        r["port_after"] = getattr(keyboard, "port", "")
    except Exception as e:
        r["reconnected"] = False
        r["reconnect_error"] = repr(e)
    return JSONResponse(r)


@app.post("/arduino/test")
def arduino_test(token: str = Query(""), key: str = Query("n5"),
                 times: int = Query(3)):
    """送測試訊號並回報板子是否回 OK。

    預設用小鍵盤 5(N5):遊戲裡通常沒綁,而且在記事本裡看得到字元,一眼確認 HID 通了。
    回 OK 的次數才是「韌體收到並執行」的證據 —— 只看「送出成功」只證明寫進了序列埠。"""
    _check_owner(token)
    return JSONResponse(firmware.test_signal(keyboard, token=key, times=times))


# ===== 解除卡死：一次放開所有鍵盤鍵與滑鼠鍵(程式被強殺後韌體仍按著某鍵時用) =====
@app.post("/input/release")
def input_release(token: str = Query("")):
    _check_owner(token)
    idle_mode.stop()                       # 先停掛機(會放開它按著的移動鍵)
    calib.stop()                           # 校準也停(會放開方向鍵)
    try:
        keyboard.release_all()             # 對所有已知鍵送 UP:
    except Exception as e:
        print(f"[release] 鍵盤釋放錯誤: {e}")
    for b in ("left", "right", "middle"):
        try:
            mouse.button_up(b)
        except Exception:
            pass
    print("[release] 已解除卡死:放開所有鍵盤/滑鼠鍵")
    return JSONResponse({"ok": True})


# ===== 訪客自助功能（任何有效 token 可用;皆為受限動作,無法作惡） =====
# 畫質：只接受預設檔名稱,不接受任意參數(防灌爆 CPU/頻寬的極端值)。
GUEST_QUALITY_PRESETS = {
    "high": {"width": 1600, "quality": 80, "fps": 30},
    "mid":  {"width": 1280, "quality": 60, "fps": 30},
    "low":  {"width": 960,  "quality": 40, "fps": 20},
}


@app.post("/guest/quality")
def guest_quality(token: str = Query(""), level: str = Query("mid")):
    """訪客畫質選擇（低/中/高 預設檔）。調整共用的 MJPEG 擷取參數。"""
    _check(token)
    p = GUEST_QUALITY_PRESETS.get(level)
    if p is None:
        raise HTTPException(status_code=400, detail="bad level")
    return JSONResponse(screen.update(**p))


@app.post("/guest/focus")
def guest_focus(token: str = Query("")):
    """訪客「對準視窗」：只會鎖定並聚焦 MapleStory 目標視窗——
    訪客無法指定任意視窗(與主人的 /window/focus 不同)。"""
    _check(token)
    ok = video_pipeline.guard_focus(GUARD_EXE)   # 統一焦點守衛(訪客)
    return JSONResponse({"ok": ok})


# ===== 遠端分享中控（密碼管理僅主人；訪客只能查剩餘時間） =====
def _clamp_hours(h, default):
    """延長/產生的時數：0.5 為一格，範圍 0.5 ~ 24 小時。"""
    try:
        h = float(h)
    except (TypeError, ValueError):
        h = default
    return min(24.0, max(0.5, h))


def _owner_remote_state():
    return {"guest": False, **remote_access.info(include_password=True),
            "tunnel": tunnel.status()}


@app.get("/remote/info")
def remote_info(request: Request, token: str = Query("")):
    """主人：完整狀態(含密碼與隧道網址)。訪客：只有剩餘時間。
    經隧道進來一律降權為訪客視角——就算主 token 外洩也看不到密碼。"""
    _check(token)
    if _is_owner(token) and not _untrusted(request.headers, request.client):
        return JSONResponse(_owner_remote_state())
    return JSONResponse({"guest": True, **remote_access.info()})


def _reject_if_idle():
    if idle_mode.is_running():
        raise HTTPException(status_code=409,
                            detail="閒置掛機模式進行中，請先關閉閒置模式再出租")


@app.post("/remote/new")
def remote_new(token: str = Query(""), hours: float = Query(None)):
    """產生新短密碼（舊的立即失效）。預設 0.5 小時，可帶 hours 自訂。"""
    _check_owner(token)
    _reject_if_idle()
    pw, _exp = remote_access.generate(_clamp_hours(hours, REMOTE_TTL_HOURS) * 3600)
    print(f"[remote] 已產生連線密碼: {pw}")
    return JSONResponse(_owner_remote_state())


@app.post("/remote/extend")
def remote_extend(token: str = Query(""), hours: float = Query(0.5)):
    """延長現有密碼有效期（+0.5h 一格或自訂）。訪客不能自我續期。"""
    _check_owner(token)
    if remote_access.extend(_clamp_hours(hours, 0.5) * 3600) is None:
        raise HTTPException(status_code=400, detail="no active password")
    return JSONResponse(_owner_remote_state())


@app.post("/remote/revoke")
def remote_revoke(token: str = Query("")):
    """立即撤銷短密碼：訪客 WS 斷線、MJPEG 中止。"""
    _check_owner(token)
    remote_access.revoke()
    print("[remote] 連線密碼已撤銷")
    return JSONResponse(_owner_remote_state())


@app.post("/remote/tunnel/start")
def remote_tunnel_start(token: str = Query("")):
    """啟動 Cloudflare Quick Tunnel；若尚無有效密碼順便產生一組。"""
    _check_owner(token)
    _reject_if_idle()
    if not remote_access.info()["active"]:
        pw, _exp = remote_access.generate(REMOTE_TTL_HOURS * 3600)
        print(f"[remote] 已產生連線密碼: {pw}")
    tunnel.start(PORT)
    return JSONResponse(_owner_remote_state())


@app.post("/remote/tunnel/stop")
def remote_tunnel_stop(token: str = Query("")):
    """停止隧道（遠端網址立即失效；短密碼狀態不變，可另行撤銷）。"""
    _check_owner(token)
    tunnel.stop()
    return JSONResponse(_owner_remote_state())


# ===== 輸入：WebSocket =====
# 前端送 JSON，type 對應動作：
#   {"t":"kt","k":"e"}            鍵盤 tap
#   {"t":"kd","k":"w"}            鍵盤按住
#   {"t":"ku","k":"w"}            鍵盤釋放
#   {"t":"mm","dx":10,"dy":-3}    滑鼠相對移動
#   {"t":"mms","dx":..,"dy":..}   滑鼠平滑移動(視角)
#   {"t":"md","b":"left"}         滑鼠按下
#   {"t":"mu","b":"left"}         滑鼠釋放
#   {"t":"mc","b":"left"}         滑鼠點擊
def _dispatch(msg):
    t = msg.get("t")
    if t == "kt":
        keyboard.tap(msg["k"])
    elif t == "kd":
        keyboard.key_down(msg["k"])
    elif t == "ku":
        keyboard.key_up(msg["k"])
    elif t == "mm":
        mouse.move_relative(msg.get("dx", 0) * MOUSE_SENSITIVITY,
                            msg.get("dy", 0) * MOUSE_SENSITIVITY)
        video_pipeline.clamp_cursor()      # 鎖在目標視窗內
    elif t == "mms":
        mouse.move_relative_smooth(msg.get("dx", 0) * MOUSE_SENSITIVITY,
                                   msg.get("dy", 0) * MOUSE_SENSITIVITY)
        video_pipeline.clamp_cursor()
    elif t == "ma":       # 絕對映射(電腦端)：目標與目前游標的差值，用相對移動精準定位
        d = video_pipeline.abs_delta(msg.get("x", 0), msg.get("y", 0))
        if d and (d[0] or d[1]):
            mouse.move_relative(*d)
    elif t == "mw":       # 滾輪：優先 Arduino 硬體(反作弊安全)，否則軟體
        n = int(msg.get("d", 0))
        if n:
            if getattr(keyboard, "connected", False):
                keyboard.wheel(n)
            else:
                soft_wheel(n * 120)
    elif t == "md":
        mouse.button_down(msg.get("b", "left"))
    elif t == "mu":
        mouse.button_up(msg.get("b", "left"))
    elif t == "mc":
        mouse.click(msg.get("b", "left"))


_guest_ws = 0            # 訪客 WS 併發計數


@app.websocket("/ws/input")
async def ws_input(ws: WebSocket, token: str = Query("")):
    global _guest_ws
    if not _token_ok(token):
        await ws.close(code=1008)
        return
    # 不信任來源(隧道/公網)的連線一律視為訪客——就算主 token 外洩，
    # 從公網也拿不到完整控制權（只能在家裡 LAN/Tailscale 使用主權限）。
    guest = (not _is_owner(token)) or _untrusted(ws.headers, ws.client)
    if guest and _guest_ws >= GUEST_MAX_CONN:
        await ws.close(code=1013)      # try again later（未 accept 的快速拒絕）
        return
    await ws.accept()
    if guest:
        # accept 之後才做精確計數：檢查與 +=1 之間沒有 await，事件迴圈單執行緒下
        # 為原子操作,兩條訪客連線同時進來也不會突破 GUEST_MAX_CONN 上限(超賣)。
        if _guest_ws >= GUEST_MAX_CONN:
            await ws.close(code=1013)
            return
        _guest_ws += 1
    print(f"[WS] 客戶端連線{'(訪客)' if guest else ''}")

    # 訪客輸入防護（全在伺服器端強制,前端用 F12 改掉也繞不過）：
    #   * 白名單：僅鍵盤 4/left/right 的 kt/kd/ku,滑鼠/滾輪訊息一律丟棄
    #   * 冷卻：同一鍵兩次「按下」需間隔 GUEST_COOLDOWN 秒(擋本機自動連點器)
    #   * 放開(ku)永遠放行,避免冷卻造成卡鍵
    last_press = {}

    def guest_allow(m):
        t = m.get("t")
        if t not in ("kt", "kd", "ku") or m.get("k") not in GUEST_KEYS:
            return False
        if t == "ku":
            return True
        now = time.monotonic()
        if now - last_press.get(m["k"], 0.0) < GUEST_COOLDOWN:
            return False
        last_press[m["k"]] = now
        return True
    # 告知前端目前身分：訪客端會切換成「僅 4/←/→ 螢幕按鈕」的受限介面
    try:
        await ws.send_text(json.dumps({"t": "mode", "guest": guest,
                                       "remain": remote_access.info()["remaining_seconds"] if guest else 0}))
    except Exception:
        pass

    # 新連線先清掉可能殘留的修飾鍵：上次若在「按著鍵」時關頁,Arduino 會一直按住,
    # 卡住的修飾鍵(尤其 Alt/Ctrl)會讓字母鍵變成快捷鍵而「完全沒反應」,方向鍵卻正常。
    # 訪客連線不做：不能讓訪客動到主人現場按著的修飾鍵。
    if not guest:
        for _k in ("shift", "ctrl", "alt"):
            keyboard.key_up(_k)
    held_keys = set()          # 本連線「實際送出」按著的鍵,斷線時全數放開

    async def cursor_loop():
        # 定期回傳游標在擷取區內的正規化座標，讓網頁畫虛擬游標（遊戲藏游標也看得到）
        try:
            while True:
                c = video_pipeline.cursor_norm()
                if c is not None:
                    await ws.send_text(json.dumps({"t": "cur", "x": round(c[0], 4), "y": round(c[1], 4)}))
                await asyncio.sleep(0.05)
        except Exception:
            pass

    async def guest_watchdog():
        # 訪客看門狗：密碼到期/被撤銷 → 立即斷線（不能只靠連線時那一次驗證）。
        # 每 10 秒回報剩餘時間，前端顯示倒數。
        try:
            i = 0
            while True:
                if not remote_access.still_valid(token):
                    await ws.send_text(json.dumps({"t": "bye", "reason": "expired"}))
                    await ws.close(code=1008)
                    return
                if i % 10 == 0:
                    await ws.send_text(json.dumps(
                        {"t": "mode", "guest": True,
                         "remain": remote_access.info()["remaining_seconds"]}))
                i += 1
                await asyncio.sleep(1.0)
        except Exception:
            pass

    # 訪客沒有滑鼠 → 不需要游標回報，改跑到期看門狗
    sender = asyncio.create_task(guest_watchdog() if guest else cursor_loop())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            # 訪客：白名單+冷卻沒過的訊息直接丟棄(也不進 held 追蹤,
            # 否則斷線清理會替「從未按下的鍵」補發 key_up,干擾主人現場操作)
            if guest and not guest_allow(msg):
                continue
            # 追蹤按著的鍵,斷線時才能全數放開(避免卡住的鍵讓字母鍵失效)
            t = msg.get("t")
            if t == "kd":
                held_keys.add(msg.get("k"))
            elif t == "ku":
                held_keys.discard(msg.get("k"))
            # 輸入分派本身很快(佇列/DLL 呼叫)，直接在事件迴圈執行即可。
            # 必須 try/except：否則單筆訊息讓 _dispatch 拋例外就會衝出迴圈、
            # 關閉整條 WS(手機顯示「斷線,重連中」，重連又被下一筆踢掉)。
            try:
                _dispatch(msg)
            except Exception as e:
                print(f"[WS] dispatch 錯誤(已忽略): {e!r} msg={msg}")
    except WebSocketDisconnect:
        print("[WS] 客戶端離線")
    finally:
        sender.cancel()
        if guest:
            _guest_ws -= 1
        # 斷線時放開所有「本連線實際按下」的鍵,避免卡住下次連線。
        # 滑鼠鍵只有主人需要放(訪客根本按不了滑鼠,放了反而干擾主人現場)。
        for k in list(held_keys):
            keyboard.key_up(k)
        if not guest:
            for b in ("left", "right", "middle"):
                mouse.button_up(b)


def _cleanup_tunnel(*_a):
    """出租進程與網址的保底清理。Job Object 已保證 cloudflared 隨行程死亡，
    這裡再主動 stop() 一次，讓正常關閉/Ctrl+C/關視窗都盡快收掉網址與進程。"""
    try:
        tunnel.stop()
    except Exception:
        pass


# lifespan shutdown 不一定跑得到(直接關 console 視窗時)，故再掛 atexit + 訊號。
import atexit
import signal
atexit.register(_cleanup_tunnel)
for _sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
    _s = getattr(signal, _sig, None)
    if _s is not None:
        try:
            signal.signal(_s, lambda *_a: (_cleanup_tunnel(), os._exit(0)))
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
