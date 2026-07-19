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
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse

from config import AUTH_TOKEN, HOST, PORT, TARGET_FPS, MOUSE_SENSITIVITY
from arduino import ArduinoKeyboard
from kmbox import KMouse
from capture import ScreenCapture
from soft_mouse import wheel as soft_wheel
import video_pipeline
import clipboard

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


@asynccontextmanager
async def lifespan(app):
    global mouse
    _check_token_strength()
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
    import audio_pipeline
    audio_pipeline.stop()
    keyboard.close()
    mouse.close()
    video_pipeline.stop()
    screen.stop()


app = FastAPI(title="maple 遠端遊玩", lifespan=lifespan)


def _check(token):
    if token != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")


# ===== 首頁 =====
@app.get("/")
def index():
    return FileResponse(
        os.path.join(WEB, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ===== 影像備援：MJPEG 串流（WebRTC 不可用時才用；延遲較高） =====
@app.get("/video")
def video(token: str = Query("")):
    _check(token)
    screen.ensure_started()

    async def gen():
        interval = 1.0 / max(1, TARGET_FPS)
        boundary = b"--frame\r\n"
        while True:
            frame = screen.latest()
            if frame is not None:
                yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(interval)

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ===== 畫面設定：讀取 / 即時調整（WebRTC 管線：螢幕 / FPS / 位元率） =====
@app.get("/config/video")
def get_video_config(token: str = Query("")):
    _check(token)
    cfg = video_pipeline.settings()
    try:
        cfg["monitor_count"] = screen.monitor_count()
    except Exception:
        cfg["monitor_count"] = 1
    return JSONResponse(cfg)


@app.post("/config/video")
async def set_video_config(request: Request, token: str = Query("")):
    _check(token)
    body = await request.json()
    return JSONResponse(video_pipeline.apply(
        source=body.get("source"), window=body.get("window"), hwnd=body.get("hwnd"),
        monitor=body.get("monitor"), fps=body.get("fps"), bitrate=body.get("bitrate"),
        scale=body.get("scale"), gray=body.get("gray"),
    ))


# ===== 視窗清單（供手機端挑選要擷取的視窗） =====
@app.get("/windows")
def list_windows(token: str = Query("")):
    _check(token)
    return JSONResponse({"windows": video_pipeline.list_windows()})


# ===== 注視：把目標視窗帶到前景，確保鍵鼠輸入送進去 =====
@app.post("/window/focus")
def window_focus(token: str = Query("")):
    _check(token)
    return JSONResponse({"ok": video_pipeline.focus_target()})


# ===== 影像啟動 / 停止（注視畫面時由前端呼叫） =====
@app.post("/video/start")
def video_start(token: str = Query("")):
    _check(token)
    ok = video_pipeline.ensure_running()
    import audio_pipeline
    audio_pipeline.start()          # 獨立音訊管線;失敗不影響影像
    return JSONResponse({"ok": ok})


@app.post("/video/stop")
def video_stop(token: str = Query("")):
    _check(token)
    video_pipeline.stop()
    import audio_pipeline
    audio_pipeline.stop()
    return JSONResponse({"ok": True})


# ===== 剪貼簿：把操作端文字複製到被控端(主機) =====
@app.post("/clipboard")
async def set_clip(request: Request, token: str = Query("")):
    _check(token)
    body = await request.json()
    return JSONResponse({"ok": clipboard.set_clipboard(body.get("text", ""))})


# ===== 狀態（硬體連線診斷） =====
@app.get("/status")
def status(token: str = Query("")):
    _check(token)
    return JSONResponse({
        "keyboard": {"arduino": getattr(keyboard, "connected", False)},
        "mouse": {"connected": getattr(mouse, "connected", False),
                  "software": getattr(mouse, "software", False)},
        "video": video_pipeline.settings(),
    })


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


@app.websocket("/ws/input")
async def ws_input(ws: WebSocket, token: str = Query("")):
    if token != AUTH_TOKEN:
        await ws.close(code=1008)
        return
    await ws.accept()
    print("[WS] 客戶端連線")

    # 新連線先清掉可能殘留的修飾鍵：上次若在「按著鍵」時關頁,Arduino 會一直按住,
    # 卡住的修飾鍵(尤其 Alt/Ctrl)會讓字母鍵變成快捷鍵而「完全沒反應」,方向鍵卻正常。
    for _k in ("shift", "ctrl", "alt"):
        keyboard.key_up(_k)
    held_keys = set()          # 本連線目前按著的鍵,斷線時全數放開

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

    sender = asyncio.create_task(cursor_loop())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
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
        # 斷線時放開所有還按著的鍵盤鍵(尤其修飾鍵)與滑鼠鍵,避免卡住下次連線
        for k in list(held_keys):
            keyboard.key_up(k)
        for b in ("left", "right", "middle"):
            mouse.button_up(b)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
