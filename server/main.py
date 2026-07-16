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

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(BASE, "..", "web"))

keyboard = ArduinoKeyboard()
mouse = KMouse()
screen = ScreenCapture()


@asynccontextmanager
async def lifespan(app):
    keyboard.start()
    mouse.start()
    screen.start()
    yield
    keyboard.close()
    mouse.close()
    screen.stop()


app = FastAPI(title="maple 遠端遊玩", lifespan=lifespan)


def _check(token):
    if token != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")


# ===== 首頁 =====
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


# ===== 影像：MJPEG 串流 =====
@app.get("/video")
def video(token: str = Query("")):
    _check(token)

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


# ===== 畫面設定：讀取 / 即時調整 =====
@app.get("/config/video")
def get_video_config(token: str = Query("")):
    _check(token)
    cfg = screen.settings()
    cfg["monitor_count"] = screen.monitor_count()
    return JSONResponse(cfg)


@app.post("/config/video")
async def set_video_config(request: Request, token: str = Query("")):
    _check(token)
    body = await request.json()
    return JSONResponse(screen.update(
        monitor=body.get("monitor"), width=body.get("width"),
        quality=body.get("quality"), fps=body.get("fps"),
    ))


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
    elif t == "mms":
        mouse.move_relative_smooth(msg.get("dx", 0) * MOUSE_SENSITIVITY,
                                   msg.get("dy", 0) * MOUSE_SENSITIVITY)
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
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            # 輸入分派本身很快(佇列/DLL 呼叫)，直接在事件迴圈執行即可
            _dispatch(msg)
    except WebSocketDisconnect:
        print("[WS] 客戶端離線")
    finally:
        # 斷線時放開所有滑鼠鍵，避免卡住
        for b in ("left", "right", "middle"):
            mouse.button_up(b)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
