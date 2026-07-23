# -*- coding: utf-8 -*-
"""掛機(自動)模式基礎：MapleStory 小地圖偵測。

第一步：在 maplestory.exe 視窗左上角找到「小地圖」的位置與大小(自適應——
不同地圖的小地圖寬高不同)，並在小地圖內找角色的黃色圓點。

偵測策略(經 4 張實機地圖驗證定案——弓箭手村/舞會場/桃源境/怪物公園,
小地圖畫布尺寸從 461x90 到 334x48 都有,完全自適應)：
  * 小地圖畫布：底色不可靠(深藍/近黑/半透明隨地圖變)、輪廓法會與場景黏連。
    唯一跨地圖穩定的特徵是【畫布上下邊框線】——貫穿整個畫布寬度的 2~4px 亮線,
    且上下兩條的 x 範圍完全相同。故逐列找「最長亮段」,把 x 範圍相同(±8px)的
    列分組;同組內分成上下兩簇(邊框厚度 2~4 列)即為畫布上下界。左右直邊在
    半透明地圖會淹沒在場景裡,不做硬驗證,改用位置(靠左上)過濾+黃點加分。
    連續多幀取中位數穩定輸出。
  * 角色黃點：先把影像【顏色反白】(255-BGR) 再轉 HSV——黃色反白後變藍
    (H≈116)、其他玩家的紅點反白後變青(H≈90)。紅色在正常 HSV 的色相環上
    跨 0/179 迴繞(要兩段範圍)、且與黃色( H≈25 )距離近容易誤判;反白後
    兩者都落在連續區段、相距 26 級,單一範圍即可乾淨分離。

來源限制：一律鎖定 maplestory.exe 視窗(WGC 視窗表面,被遮蓋照拍)；
WGC 不可用時退回螢幕區域裁切。拿不到遊戲視窗就回 None(絕不掃整個桌面)。
"""
import collections
import threading
import time

import numpy as np
import cv2

from config import GUARD_EXE

_lock = threading.Lock()          # 序列化偵測(HTTP 執行緒池並發輪詢時)
_history = collections.deque(maxlen=5)   # 最近幾次 bbox,取中位數抗單幀抖動
_last = {"found": False}          # 最近一次偵測結果(給 /minimap/status)


# ---------- 取得遊戲視窗影格 ----------
def _grab_window():
    """maplestory.exe 視窗的 BGR 影格；拿不到回 None。"""
    import video_pipeline
    import wgc
    if not video_pipeline.force_window_target(GUARD_EXE):
        return None                       # 遊戲沒開:不偵測、不退回桌面
    hwnd = video_pipeline.state["hwnd"]
    if wgc.ensure(hwnd):
        f = wgc.latest(max_age=1.0)
        if f is None:                     # 剛啟動:等第一格
            for _ in range(10):
                time.sleep(0.1)
                f = wgc.latest(max_age=1.0)
                if f is not None:
                    break
        if f is not None:
            return cv2.cvtColor(f, cv2.COLOR_BGRA2BGR)
    bbox = video_pipeline.window_abs_bbox(hwnd)   # 備援:螢幕區域裁切
    if not bbox:
        return None
    import mss
    with mss.mss() as sct:
        img = np.asarray(sct.grab(bbox))
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


# ---------- 角色黃點(顏色反白後找藍) ----------
def _player_dot(bgr):
    """在(小地圖)影像內找角色黃點。回 (x, y) 或 None。
    反白後黃→藍:H 108~124、S/V 夠高;取最大連通塊的質心。"""
    inv = cv2.bitwise_not(bgr)
    hsv = cv2.cvtColor(inv, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (108, 120, 150), (124, 255, 255))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    best, area = None, 0
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if 2 <= a <= 200 and a > area:    # 黃點很小;排除大片誤判(如 UI 黃字區)
            area = a
            best = (int(cent[i][0]), int(cent[i][1]))
    return best


# ---------- 小地圖畫布偵測(邊框線配對法) ----------
def _longest_run(row):
    """單列布林陣列中最長的連續 True 段。回 (start, end, length)。"""
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return (0, 0, 0)
    splits = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[0, splits + 1]
    ends = np.r_[splits, idx.size - 1]
    lens = idx[ends] - idx[starts] + 1
    k = int(lens.argmax())
    return (int(idx[starts[k]]), int(idx[ends[k]]), int(lens[k]))


def _row_clusters(ys):
    """把列號依連續性(相鄰 ≤4)分簇。回 [[y,...], ...](已排序)。"""
    ys = sorted(ys)
    out = [[ys[0]]]
    for v in ys[1:]:
        if v - out[-1][-1] <= 4:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _candidates(frame):
    """左上 ROI 內的畫布候選 [(x,y,w,h), ...]。
    逐列取最長亮段(>150),x 範圍相同(±8)者分組;同組有上下兩簇亮線
    (畫布上下邊框)且間距/位置合理者成為候選。"""
    h, w = frame.shape[:2]
    roi_w, roi_h = int(w * 0.55), int(h * 0.65)
    gray = cv2.cvtColor(frame[:roi_h, :roi_w], cv2.COLOR_BGR2GRAY)
    bright = gray > 150
    groups = {}                            # (start,end) -> [row, ...]
    for y in range(roi_h):
        s, e, L = _longest_run(bright[y])
        if L < 140 or L > roi_w * 0.5:     # 太短不是邊框;太長是場景橫貫亮帶
            continue
        if s > roi_w * 0.25 or y > roi_h * 0.75:   # 畫布必靠左、偏上
            continue
        placed = False
        for k in list(groups):
            if abs(k[0] - s) <= 8 and abs(k[1] - e) <= 8:
                groups[k].append(y)
                placed = True
                break
        if not placed:
            groups[(s, e)] = [y]
    out = []
    for (s, e), ys in groups.items():
        cl = _row_clusters(ys)
        if len(cl) < 2:
            continue
        y1 = cl[0][-1]                     # 上邊框最下列
        y2 = cl[-1][0]                     # 下邊框最上列
        # 高度 30~450:實測最扁的怪物公園 48px、一般 90~160px
        if not (30 <= y2 - y1 <= 450) or y1 > roi_h * 0.45:
            continue
        out.append((s, y1 + 1, e - s + 1, y2 - y1 - 1))   # 內部區域(不含邊框線)
    return out


def _detect(frame):
    """回 (bbox, dot, cands)。bbox=(x,y,w,h) 或 None;dot=黃點在 frame 座標或 None。"""
    cands = _candidates(frame)
    best, best_dot, best_score = None, None, -1.0
    for (x, y, cw, ch) in cands:
        crop = frame[y:y + ch, x:x + cw]
        dot = _player_dot(crop)
        score = cw * ch + (1e9 if dot else 0)   # 含黃點的候選絕對優先;其次取最大
        if score > best_score:
            best_score = score
            best = (x, y, cw, ch)
            best_dot = (x + dot[0], y + dot[1]) if dot else None
    return best, best_dot, cands


def _stable_bbox(bbox):
    """把本次 bbox 丟進歷史,回各分量中位數(抗單幀抖動)。None 就清空歷史。"""
    if bbox is None:
        _history.clear()
        return None
    _history.append(bbox)
    arr = np.array(_history)
    return tuple(int(v) for v in np.median(arr, axis=0))


# ---------- 對外 API ----------
def status():
    """最近一次偵測結果(不觸發新偵測;由 frame 端點驅動)。"""
    return dict(_last)


def detect_once():
    """抓一格 → 偵測 → 更新 _last。回 (frame, bbox, dot, cands);拿不到影格回 (None,)*4。"""
    global _last
    with _lock:
        frame = _grab_window()
        if frame is None:
            _last = {"found": False, "error": "拿不到 MapleStory 視窗影格(遊戲開著嗎?)"}
            return None, None, None, None
        bbox, dot, cands = _detect(frame)
        bbox = _stable_bbox(bbox)
        h, w = frame.shape[:2]
        if bbox:
            _last = {"found": True, "x": bbox[0], "y": bbox[1],
                     "w": bbox[2], "h": bbox[3],
                     "frame_w": w, "frame_h": h,
                     "dot": {"x": dot[0] - bbox[0], "y": dot[1] - bbox[1]} if dot else None}
        else:
            _last = {"found": False, "frame_w": w, "frame_h": h}
        return frame, bbox, dot, cands


def debug_jpeg(view="annot", quality=80):
    """偵測並回傳標註後 JPEG bytes(遠端預覽用);拿不到影格回 None。
    view='annot':整個視窗縮圖+標註(灰=候選、綠=選定、洋紅圈=角色黃點)。
    view='crop' :只回小地圖裁切放大 2 倍(沒偵測到就回左上區域)。"""
    frame, bbox, dot, cands = detect_once()
    if frame is None:
        return None
    if view == "crop":
        if bbox:
            x, y, w, h = bbox
            crop = frame[y:y + h, x:x + w].copy()
            if dot:
                cv2.drawMarker(crop, (dot[0] - x, dot[1] - y), (255, 0, 255),
                               cv2.MARKER_CROSS, 12, 1)
            crop = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_NEAREST)
            out = crop
        else:
            fh, fw = frame.shape[:2]
            out = frame[:int(fh * 0.4), :int(fw * 0.4)]
    else:
        out = frame.copy()
        for (x, y, w, h) in (cands or []):
            cv2.rectangle(out, (x, y), (x + w, y + h), (128, 128, 128), 1)
        if bbox:
            x, y, w, h = bbox
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(out, f"{w}x{h}", (x, y + h + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        if dot:
            cv2.circle(out, dot, 6, (255, 0, 255), 2)
        fh, fw = out.shape[:2]
        if fw > 1100:                     # 遠端預覽夠看就好,省頻寬
            nh = int(fh * 1100 / fw)
            out = cv2.resize(out, (1100, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    return buf.tobytes() if ok else None
