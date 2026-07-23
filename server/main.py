# -*- coding: utf-8 -*-
"""FastAPI 遠端遊玩伺服器。

跑在家裡的遊戲主機上：
  * 手機瀏覽器連進來 -> 看畫面(MJPEG) + 送輸入(WebSocket)
  * 鍵盤輸入 -> Arduino HID(序列埠)
  * 滑鼠輸入 -> km.dll(KMBox 硬體)
兩者皆為硬體訊號，遊戲/反作弊偵測不到是遠端輸入。

安全性：所有端點需帶 ?token=，且務必只透過 Tailscale/VPN 連線，勿裸奔公網。
"""
import asyncio
import hmac
import ipaddress
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse

from config import (AUTH_TOKEN, HOST, PORT, TARGET_FPS, MOUSE_SENSITIVITY,
                    REMOTE_MODE, REMOTE_TTL_HOURS, GUEST_COOLDOWN, GUEST_MAX_CONN,
                    GUARD_TITLE)
from arduino import ArduinoKeyboard
from kmbox import KMouse
from capture import ScreenCapture
from soft_mouse import wheel as soft_wheel
import video_pipeline
import clipboard
import remote_access
import tunnel

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(BASE, "..", "web"))


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
    while True:
        try:
            if remote_access.info()["active"]:
                video_pipeline.force_window_target(GUARD_TITLE)
                video_pipeline.enforce_focus(GUARD_TITLE)
        except Exception:
            pass
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app):
    global mouse
    _check_token_strength()
    if REMOTE_MODE:
        _print_remote_banner()
        tunnel.start(PORT)
    guard = asyncio.create_task(_rental_guard_loop())
    keyboard.start()
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
    # 影像主力為 WebRTC(MediaMTX+ffmpeg)，由 /video/start 啟動 ffmpeg。
    yield
    guard.cancel()
    import audio_pipeline
    audio_pipeline.stop()
    keyboard.close()
    mouse.close()
    video_pipeline.stop()
    screen.stop()
    tunnel.stop()


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


@app.get("/")
def index(request: Request):
    page = "guest.html" if _untrusted(request.headers, request.client) else "index.html"
    return FileResponse(os.path.join(WEB, page), headers=_NO_CACHE)


@app.get("/guest")
def guest_page():
    """訪客頁直接入口（LAN 測試、或把此路徑分享給訪客用）。"""
    return FileResponse(os.path.join(WEB, "guest.html"),
                        headers={**_NO_CACHE, **_SEC_HEADERS})


# ===== 影像備援：MJPEG 串流（WebRTC 不可用時才用；延遲較高） =====
# 訪客併發計數：一組密碼開太多串流會塞爆家用上傳頻寬(DoS)，超過上限直接拒絕。
_guest_streams = 0


@app.get("/video")
def video(token: str = Query("")):
    _check(token)
    guest = not _is_owner(token)
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
                if guest and not video_pipeline.target_window_valid(GUARD_TITLE):
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
    return JSONResponse(video_pipeline.apply(
        source=body.get("source"), window=body.get("window"), hwnd=body.get("hwnd"),
        monitor=body.get("monitor"), fps=body.get("fps"), bitrate=body.get("bitrate"),
        scale=body.get("scale"), gray=body.get("gray"),
    ))


# ===== 視窗清單（供手機端挑選要擷取的視窗） =====
@app.get("/windows")
def list_windows(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"windows": video_pipeline.list_windows()})


# ===== 注視：把目標視窗帶到前景，確保鍵鼠輸入送進去 =====
@app.post("/window/focus")
def window_focus(token: str = Query("")):
    _check_owner(token)
    return JSONResponse({"ok": video_pipeline.focus_target()})


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
    ok = video_pipeline.force_window_target(GUARD_TITLE)
    if ok:
        video_pipeline.enforce_focus(GUARD_TITLE)
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


@app.post("/remote/new")
def remote_new(token: str = Query(""), hours: float = Query(None)):
    """產生新短密碼（舊的立即失效）。預設 0.5 小時，可帶 hours 自訂。"""
    _check_owner(token)
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
        await ws.close(code=1013)      # try again later
        return
    await ws.accept()
    if guest:
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
