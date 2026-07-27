# -*- coding: utf-8 -*-
"""死亡偵測與自動復活（巡邏中）。

死亡時遊戲跳出對話框:「確定要在當前地圖中復活嗎?」 + 綠色「確認」 / 灰色「取消」。
偵測到就把滑鼠移到「確認」上點一下,巡邏即可繼續。

【偵測方式:圖形識別,不做 OCR】
「確認」鈕是飽和的黃綠色橫向圓角矩形,右邊緊接一顆同高的灰色「取消」鈕。
單看綠色會誤判(符文箭頭也可能是黃綠色),所以要求三個條件同時成立:
  1. 一塊黃綠色、寬高比約 2~3.5 的矩形(箭頭是接近正方的 1:1,寬高比直接分開)
  2. 它的右邊、同一水平線上有一塊低飽和(灰)的相近大小矩形
  3. 連續 CONFIRM_HITS 次偵測到才動手 —— 誤點滑鼠的代價比晚一秒復活高得多

【座標】偵測是在遊戲視窗影格內做的(frames.get()),點擊需要絕對螢幕座標,
所以要加上 window_abs_bbox 的原點。視窗有邊框時影格與視窗矩形同尺寸(WGC 取的是
視窗表面),所以直接相加即可;若尺寸不符則按比例換算。
"""
import os
import tempfile
import threading
import time

import cv2
import numpy as np

_DEBUG_DIR = os.path.join(tempfile.gettempdir(), "maple_rune")
os.makedirs(_DEBUG_DIR, exist_ok=True)
_SHOT = os.path.join(_DEBUG_DIR, "revive_shot.png")

# ---- 可調參數（需真死亡畫面校準）----
# 搜尋範圍(佔畫面比例)。對話框在畫面中央偏上,不必掃全螢幕(減少誤判來源)。
SEARCH = (0.25, 0.75, 0.20, 0.80)      # (y0, y1, x0, x1)
GREEN_LO, GREEN_HI = (28, 90, 120), (52, 255, 255)   # 黃綠「確認」鈕
BTN_MIN_AREA, BTN_MAX_AREA = 600, 12000
BTN_AR_MIN, BTN_AR_MAX = 1.8, 4.0      # 寬高比:按鈕是橫長方,符文箭頭≈1:1
GRAY_SAT_MAX = 60                      # 「取消」鈕:低飽和
PAIR_DY = 14                           # 兩鈕中心 y 差上限
PAIR_DX_MAX = 240                      # 兩鈕水平距離上限
CONFIRM_HITS = 2                       # 連續幾次偵測到才點(防誤點)
CLICK_COOLDOWN = 8.0                   # 點過之後多久內不再點(等畫面切換)

_state = {"hits": 0, "last_click": 0.0, "clicks": 0, "detects": 0,
          "last_box": None, "enabled": True}
_lock = threading.Lock()
_mouse = None
_focus_fn = None


def set_hooks(mouse=None, focus_fn=None):
    global _mouse, _focus_fn
    if mouse is not None:
        _mouse = mouse
    if focus_fn is not None:
        _focus_fn = focus_fn


def set_enabled(on):
    _state["enabled"] = bool(on)
    print(f"[revive] 自動復活:{'開' if _state['enabled'] else '關'}")


def status():
    s = dict(_state)
    s["cooldown_left"] = max(0.0, round(CLICK_COOLDOWN - (time.time() - _state["last_click"]), 1))
    return s


def _find_confirm(bgr):
    """回「確認」鈕在【影格座標】的中心 (cx, cy) 與外接框;找不到回 (None, None)。"""
    h, w = bgr.shape[:2]
    ry0, ry1 = int(h * SEARCH[0]), int(h * SEARCH[1])
    rx0, rx1 = int(w * SEARCH[2]), int(w * SEARCH[3])
    roi = bgr[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return None, None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    def rects(mask):
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, _l, st, _c = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        for i in range(1, n):
            a = int(st[i][cv2.CC_STAT_AREA])
            bw, bh = int(st[i][cv2.CC_STAT_WIDTH]), int(st[i][cv2.CC_STAT_HEIGHT])
            if not (BTN_MIN_AREA <= a <= BTN_MAX_AREA):
                continue
            ar = bw / max(bh, 1)
            if not (BTN_AR_MIN <= ar <= BTN_AR_MAX):
                continue
            out.append((int(st[i][cv2.CC_STAT_LEFT]), int(st[i][cv2.CC_STAT_TOP]), bw, bh))
        return out

    greens = rects(cv2.inRange(hsv, GREEN_LO, GREEN_HI))
    if not greens:
        return None, None
    # 灰色候選:低飽和且夠亮(對話框底色也偏亮,但按鈕是實心塊,面積/比例會過濾掉)
    grays = rects(cv2.inRange(hsv, (0, 0, 90), (179, GRAY_SAT_MAX, 255)))

    for g in greens:
        gcx, gcy = g[0] + g[2] / 2, g[1] + g[3] / 2
        for k in grays:
            kcx, kcy = k[0] + k[2] / 2, k[1] + k[3] / 2
            if abs(kcy - gcy) > PAIR_DY:            # 必須同一水平線
                continue
            if not (0 < kcx - gcx <= PAIR_DX_MAX):  # 灰鈕在綠鈕【右邊】
                continue
            if not (0.5 <= k[2] / max(g[2], 1) <= 2.0):   # 兩鈕寬度相近
                continue
            return (rx0 + int(gcx), ry0 + int(gcy)), (rx0 + g[0], ry0 + g[1], g[2], g[3])
    return None, None


def detect(save=True):
    """乾跑偵測:回 dict。不點滑鼠,供中控頁校準用。"""
    import frames
    frame, _is_win = frames.get()
    if frame is None:
        return {"found": False, "err": "抓不到遊戲畫面"}
    center, box = _find_confirm(frame)
    if save:
        vis = frame.copy()
        if box:
            x, y, w, h = box
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.drawMarker(vis, center, (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        cv2.imwrite(_SHOT, vis)
    return {"found": center is not None, "center": list(center) if center else None,
            "box": list(box) if box else None, "frame": [frame.shape[1], frame.shape[0]],
            "shot": _SHOT}


def _to_screen(cx, cy, frame_w, frame_h):
    """影格座標 → 絕對螢幕座標。回 (x, y) 或 None。"""
    import video_pipeline
    bb = video_pipeline.window_abs_bbox()
    if not bb:
        return None
    # WGC 取的是視窗表面,尺寸通常與視窗矩形相同;不同時按比例換算
    sx = bb["width"] / max(frame_w, 1)
    sy = bb["height"] / max(frame_h, 1)
    return int(bb["left"] + cx * sx), int(bb["top"] + cy * sy)


def check():
    """巡邏每輪呼叫。偵測到死亡對話框就點「確認」。回 True=有處理(本輪別再走位)。"""
    if not _state["enabled"] or _mouse is None:
        return False
    if time.time() - _state["last_click"] < CLICK_COOLDOWN:
        return True                      # 剛點過,等畫面切換,先別走位
    with _lock:
        import frames
        frame, _is_win = frames.get()
        if frame is None:
            return False
        center, box = _find_confirm(frame)
        if center is None:
            _state["hits"] = 0
            return False
        _state["hits"] += 1
        _state["detects"] += 1
        _state["last_box"] = list(box)
        print(f"[revive] 偵測到復活對話框 {box} (連續 {_state['hits']} 次)")
        if _state["hits"] < CONFIRM_HITS:
            return True                  # 還沒到門檻:先別走位,也先別點
        pt = _to_screen(center[0], center[1], frame.shape[1], frame.shape[0])
        if pt is None:
            print("[revive] 拿不到視窗座標,無法點擊")
            return True
        try:
            cv2.imwrite(_SHOT, frame)    # 留證:點之前的畫面
            if _focus_fn:
                _focus_fn()
            _mouse.move_to(pt[0], pt[1])
            time.sleep(0.12)             # 讓遊戲收到移動、按鈕進入 hover
            _mouse.click("left")
            _state["last_click"] = time.time()
            _state["clicks"] += 1
            _state["hits"] = 0
            print(f"[revive] 已點擊確認 @螢幕{pt}")
        except Exception as e:
            print(f"[revive] 點擊失敗: {e!r}")
        return True
