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
  * 角色黃點：實機採樣定案——角色點是【純飽和黃】HSV(28,255,254),而地圖
    金色地形/雕像等黃色裝飾 S≤170、V≤204,用 S,V≥200 嚴格範圍乾淨分離。
    紅色怪物點 H≈0/179 不在黃色範圍,無衝突。多候選時就近追蹤(取離上次
    位置最近者),短暫偵測不到時保留殘影幾秒(dot_stale),抗閃爍。

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
# 鎖定機制:地圖內容有白色橫帶時邊框配對會誤抓。連續多幀偵測到「幾乎相同」的
# bbox 才鎖定;鎖定後不再重偵測(白色內容再也干擾不到),直到手動 redetect()
# 或遊戲視窗尺寸改變才解鎖。
_locked = None                    # 鎖定的 (x,y,w,h);None=未鎖定
_locked_size = None               # 鎖定當下的影格 (w,h);視窗改尺寸即失效
_LOCK_JITTER = 12                 # 歷史內各分量最大擺動 ≤此值才視為穩定可鎖定
# 黃點追蹤:上一次角色位置(畫布內相對座標)+時間。短暫抓不到時沿用舊值
# (殘影,dot_stale=True)最多 _DOT_GHOST 秒,十字不閃爍。
_dot_last = None
_dot_ts = 0.0
_DOT_GHOST = 3.0


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


# ---------- 角色黃點 ----------
def _player_dot(bgr, last=None):
    """在(小地圖)影像內找角色黃點。回 (x, y) 或 None。

    實機採樣定案(女皇之路):角色點是【純飽和黃】BGR(0,239,254) → HSV(28,255,254);
    地圖裡的金色平台/雕像等黃色裝飾全部 S≤170、V≤204。故用 S≥200 且 V≥200 的
    嚴格範圍即可乾淨分離——之前的反白寬鬆範圍(等效 S>120)會把整片金色地形收進來,
    大 blob 搶走角色點。黃色 H≈28 不跨色相環 0/179(紅色才會),不需反白處理迴繞。
    last=(x,y):上一次角色位置;有多個候選時取最近者(就近追蹤,抗短暫誤判)。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (24, 200, 200), (34, 255, 255))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    cands = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 8 or a > 600:              # 黃點依解析度 ~10-130px;排除雜訊與大片
            continue
        w_, h_ = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if not (0.4 <= w_ / max(1, h_) <= 2.5):   # 圓點形狀(非長條亮帶)
            continue
        cands.append((int(cent[i][0]), int(cent[i][1]), a))
    if not cands:
        return None
    if last is not None:
        near = min(cands, key=lambda c: (c[0] - last[0]) ** 2 + (c[1] - last[1]) ** 2)
        return (near[0], near[1])
    best = max(cands, key=lambda c: c[2])
    return (best[0], best[1])


# ---------- 小地圖畫布偵測(邊框線配對法) ----------
def _runs(row):
    """單列布林陣列中所有連續 True 段 [(start, end, length), ...]。
    注意不能只取「最長段」——場景的亮天空/亮帶可能與邊框同列且更長,
    會把邊框段擠掉(實測:女皇之路右側亮藍天空)。"""
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return []
    splits = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[0, splits + 1]
    ends = np.r_[splits, idx.size - 1]
    return [(int(idx[s]), int(idx[e]), int(idx[e] - idx[s] + 1))
            for s, e in zip(starts, ends)]


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
    # 偵測範圍縮小到左上 40% x 50%:小地圖實測最大約佔寬 18%/高 21%(2560x1440),
    # 範圍越小,場景/其他 UI 的白色亮帶造成誤配對的機會越低。
    roi_w, roi_h = int(w * 0.40), int(h * 0.50)
    gray = cv2.cvtColor(frame[:roi_h, :roi_w], cv2.COLOR_BGR2GRAY)
    bright = gray > 150
    groups = {}                            # (start,end) -> [row, ...]
    for y in range(roi_h):
        if y > roi_h * 0.75:               # 畫布必偏上
            break
        for (s, e, L) in _runs(bright[y]):
            if L < 140 or L > roi_w * 0.5:     # 太短不是邊框;太長是場景橫貫亮帶
                continue
            if s > roi_w * 0.25:               # 畫布必靠左
                continue
            placed = False
            for k in list(groups):
                if abs(k[0] - s) <= 8 and abs(k[1] - e) <= 8:
                    if groups[k][-1] != y:
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


def _maybe_lock(median):
    """歷史滿且各分量擺動 ≤_LOCK_JITTER → 鎖定中位數 bbox。回傳鎖定與否。"""
    global _locked
    if median is None or len(_history) < _history.maxlen:
        return False
    arr = np.array(_history)
    if int((arr.max(axis=0) - arr.min(axis=0)).max()) <= _LOCK_JITTER:
        _locked = median
        print(f"[minimap] 小地圖鎖定 {median}(連續 {len(_history)} 幀穩定)")
        return True
    return False


# ---------- 對外 API ----------
def status():
    """最近一次偵測結果(不觸發新偵測;由 frame 端點驅動)。"""
    return dict(_last)


def redetect():
    """手動解除鎖定並清空歷史,下一幀起重新偵測。"""
    global _locked, _locked_size, _dot_last
    with _lock:
        _locked = None
        _locked_size = None
        _dot_last = None
        _history.clear()
    print("[minimap] 手動重新偵測:解除鎖定")


def detect_once():
    """抓一格 → 偵測 → 更新 _last。回 (frame, bbox, dot, cands);拿不到影格回 (None,)*4。
    已鎖定時直接用鎖定 bbox(不重偵測,地圖白色內容干擾不到),每幀只找黃點。"""
    global _last, _locked, _locked_size, _dot_last, _dot_ts
    with _lock:
        frame = _grab_window()
        if frame is None:
            _last = {"found": False, "locked": _locked is not None,
                     "error": "拿不到 MapleStory 視窗影格(遊戲開著嗎?)"}
            return None, None, None, None
        h, w = frame.shape[:2]
        if _locked is not None and _locked_size != (w, h):
            print(f"[minimap] 視窗尺寸變更 {_locked_size} → {(w, h)},解除鎖定")
            _locked = None
            _dot_last = None
            _history.clear()
        cands = []
        if _locked is not None:
            bbox = _locked
        else:
            raw, _d, cands = _detect(frame)
            bbox = _stable_bbox(raw)
            if _maybe_lock(bbox):
                _locked_size = (w, h)
        # 黃點每幀都在(鎖定/偵測到的)bbox 內重找——角色會動,不能沿用舊值。
        # 就近追蹤 + 短暫殘影:多候選取離上次最近;抓不到時沿用舊值 ≤_DOT_GHOST 秒。
        dot, stale = None, False
        if bbox:
            x, y, bw, bh = bbox
            d = _player_dot(frame[y:y + bh, x:x + bw], last=_dot_last)
            now = time.monotonic()
            if d:
                _dot_last, _dot_ts = d, now
            elif _dot_last is not None and now - _dot_ts <= _DOT_GHOST:
                d, stale = _dot_last, True
            else:
                _dot_last = None
            if d:
                dot = (x + d[0], y + d[1])
        if bbox:
            _last = {"found": True, "locked": _locked is not None,
                     "x": bbox[0], "y": bbox[1], "w": bbox[2], "h": bbox[3],
                     "frame_w": w, "frame_h": h,
                     "dot": {"x": dot[0] - bbox[0], "y": dot[1] - bbox[1]} if dot else None,
                     "dot_stale": stale}
        else:
            _last = {"found": False, "locked": False, "frame_w": w, "frame_h": h}
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
