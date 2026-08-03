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

from config import GUARD_EXE, PURPLE_NOTIFY_COOLDOWN
import notify

_lock = threading.Lock()          # 序列化偵測(HTTP 執行緒池並發輪詢時)
_history = collections.deque(maxlen=5)   # 最近幾次 bbox,取中位數抗單幀抖動
_hist_dot = collections.deque(maxlen=5)  # 對應每幀「有無看到角色黃點」(鎖定門檻)
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
# 紫色菱形標記(特殊NPC/玩家進圖等):出現(前一幀沒有→這一幀有)就發 Telegram,
# 冷卻 PURPLE_NOTIFY_COOLDOWN 秒內不重發。
_purple_prev = False
_purple_notify_ts = 0.0
_event_hook = None       # 事件回調(紫標出現瞬間 → 暫停巡邏);由 main 註冊


def set_event_hook(fn):
    """註冊事件回調 fn(kind, data)。目前 kind='purple' 於紫標出現瞬間觸發。"""
    global _event_hook
    _event_hook = fn
# 背景監看:掛機時前端可能沒開預覽,由此執行緒定期跑 detect_once 觸發紫標通知
_watch_stop_ev = threading.Event()
_watch_thread = None
_watch_gen = 0                    # 世代號:舊執行緒靠它自我退場(見 _watch_loop)


# ---------- 取得遊戲視窗影格 ----------
def _grab_window():
    """maplestory.exe 視窗的【原始全解析度】BGR 影格;拿不到回 None。
    統一走 frames 這個單一來源(WGC → mss 備援只在那裡實作一份)。
    符文辨識(rune)也是呼叫這個函式,與巡邏共用同一份影格。"""
    import frames
    f, _is_window = frames.get()
    return f


# ---------- 角色黃點 ----------
# 角色黃點的填充率下限(面積÷外接矩形)。
# 【必須用 _player_dot 自己的嚴格門檻(S/V>=235)量】實測:
#     角色      6x6   面積 24  填充率 0.67
#     黃召喚物 10x10  面積 20  填充率 0.20
# 取 0.45 落在兩者中間。第一版誤用寬鬆門檻(S>=110)量到的 0.89 去設 0.75,
# 結果連真正的角色(0.67)都被濾掉 —— 只是靠退路機制沒出事,等於這道防線從未生效。
DOT_MIN_FILL = 0.45


def blobs_near(cx, cy, radius=6):
    """最近一次偵測的小地圖上,(cx,cy) 附近有哪些彩色物件。回 [{dx,dy,H,S,V,area,fill}]。

    【為什麼需要】符文解除的驗證是「小地圖上的紫標消失了」,但紫標消失有兩種原因:
    真的解除了、或是【被別的東西蓋住】。陰陽師的召喚物就會蓋在符文上(使用者實測),
    那時誤判成功的代價很高 —— 紫標 hook 是邊緣觸發,那顆符文之後不會再被解。
    所以要能看見「該位置現在有沒有別的彩色物件」,有的話就不能判成功。

    座標是小地圖內的相對座標(與 status() 的 dot/purple 同一套)。"""
    with _lock:
        b = (_last.get("x"), _last.get("y"), _last.get("w"), _last.get("h"))
    if not _last.get("found") or None in b:
        return None
    frame = _grab_window()
    if frame is None:
        return None
    x, y, w, h = b
    if y + h > frame.shape[0] or x + w > frame.shape[1]:
        return None
    mm = frame[y:y + h, x:x + w]
    x0, y0 = max(0, cx - radius), max(0, cy - radius)
    x1, y1 = min(w, cx + radius + 1), min(h, cy + radius + 1)
    sub = mm[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    # 門檻刻意寬:這裡要找的是「有沒有東西」,不是「是不是某個特定物件」,
    # 收窄只會讓遮擋物漏網,而漏網就等於誤判解除成功。
    mask = ((hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 140)).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        a = int(st[i, cv2.CC_STAT_AREA])
        if a < 4:
            continue
        bw = int(st[i, cv2.CC_STAT_WIDTH])
        bh = int(st[i, cv2.CC_STAT_HEIGHT])
        px = hsv[lab == i]
        out.append({"dx": int(cen[i][0]) + x0 - cx, "dy": int(cen[i][1]) + y0 - cy,
                    "H": int(np.median(px[:, 0])), "S": int(np.median(px[:, 1])),
                    "V": int(np.median(px[:, 2])), "area": a,
                    "fill": round(a / max(1, bw * bh), 2)})
    return out


def _player_dot(bgr, last=None):
    """在(小地圖)影像內找角色黃點。回 (x, y) 或 None。

    遊戲的角色點是【固定素材】——顏色/形狀永不變化,只可能被其他 UI 遮住
    (使用者確認)。兩張不同地圖實機採樣核心色完全相同:BGR(0,239,254) →
    HSV(28,255,254)。故用精確色的窄容差匹配(H 25~31、S/V ≥235):地圖金色
    地形/雕像 S≤170 V≤204 差距極大,絕不誤收。WGC 拿的是無損 BGRA,核心
    像素不受壓縮失真影響;僅邊緣抗鋸齒像素被排除,blob 面積縮小屬預期。
    last=(x,y):上一次角色位置;有多個候選時取最近者(就近追蹤,抗短暫誤判)。

    --------------------------------------------------------------------
    召喚物排異(陰陽師)
    --------------------------------------------------------------------
    陰陽師的召喚物會在小地圖上顯示成【菱形】色塊。用【本函式自己的嚴格門檻】
    (S/V>=235)實機量到:
        角色      6x6   面積 24  填充率 0.67
        黃召喚物 10x10  面積 20  填充率 0.20
    【顏色擋不住它】用寬鬆門檻量整個菱形時黃召喚物的色相是 23、與角色的 28 差 5,
    看起來安全;但嚴格門檻只留得下它的核心亮部,那部分的色相【也是 28】,與角色相同。
    目前沒出事純粹是巧合 —— 角色面積 24 剛好比召喚物亮部的 20 大一點,而多候選時
    取的是面積最大者。召喚物外接矩形有 10x10,亮部多幾個像素就會反超,那時導航會
    整個跟著召喚物跑。

    所以改用形狀:填充率(面積÷外接矩形)角色 0.67、召喚物 0.20,差距遠大於顏色。
    【但不當硬門檻】—— 角色被 UI 遮住時會缺角、填充率跟著掉,硬擋會在那時把真的
    角色也丟掉。改成:有夠圓的候選就只從那些裡面挑,一個都沒有時才退回全部候選。
    排序也從「面積最大」改成「最圓」,理由同上:召喚物的面積本來就可能比角色大。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (25, 235, 235), (31, 255, 255))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    cands = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 6 or a > 400:              # 固定素材的核心色面積(依視窗解析度)
            continue
        w_, h_ = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if w_ > 26 or h_ > 26:            # 點很小;大塊必是別的東西
            continue
        if not (0.4 <= w_ / max(1, h_) <= 2.5):   # 圓點形狀(非長條亮帶)
            continue
        cands.append((int(cent[i][0]), int(cent[i][1]), a, a / max(1, w_ * h_)))
    if not cands:
        return None
    round_ = [c for c in cands if c[3] >= DOT_MIN_FILL]
    cands = round_ or cands           # 有圓的就只看圓的;全被遮到不圓才退回全部
    if last is not None:
        near = min(cands, key=lambda c: (c[0] - last[0]) ** 2 + (c[1] - last[1]) ** 2)
        return (near[0], near[1])
    best = max(cands, key=lambda c: c[3])   # 沒有前一點可追時,挑最圓的(而非最大的)
    return (best[0], best[1])


# ---------- 紫色菱形標記 ----------
def _purple_marks(bgr):
    """小地圖內的紫色菱形標記 [(x, y), ...]。
    同角色點:固定素材、顏色形狀永不變(使用者確認),用精確色窄容差。
    實機採樣核心色 BGR(255,102,221) → HSV(143,153,255):H 138~148、
    S 130~180、V≥235。點狀大小/形狀過濾同黃點。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (138, 130, 235), (148, 180, 255))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 6 or a > 400:
            continue
        w_, h_ = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if w_ > 26 or h_ > 26:
            continue
        if not (0.4 <= w_ / max(1, h_) <= 2.5):
            continue
        out.append((int(cent[i][0]), int(cent[i][1])))
    return out


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
        _hist_dot.clear()
        return None
    _history.append(bbox)
    arr = np.array(_history)
    return tuple(int(v) for v in np.median(arr, axis=0))


def _maybe_lock(median):
    """歷史滿、各分量擺動 ≤_LOCK_JITTER、且窗內至少一幀看過角色黃點 → 鎖定。
    黃點門檻(嚴謹):角色點永遠在小地圖上,窗內一次都沒看到 = 抓到的極可能
    不是小地圖(例:半透明活動面板的假配對)或小地圖被 UI 蓋住——都不該鎖。"""
    global _locked
    if median is None or len(_history) < _history.maxlen:
        return False
    if not any(_hist_dot):
        return False
    arr = np.array(_history)
    if int((arr.max(axis=0) - arr.min(axis=0)).max()) <= _LOCK_JITTER:
        _locked = median
        print(f"[minimap] 小地圖鎖定 {median}(連續 {len(_history)} 幀穩定+黃點確認)")
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
        _hist_dot.clear()
    print("[minimap] 手動重新偵測:解除鎖定")


def detect_once():
    """抓一格 → 偵測 → 更新 _last。回 (frame, bbox, dot, cands);拿不到影格回 (None,)*4。
    已鎖定時直接用鎖定 bbox(不重偵測,地圖白色內容干擾不到),每幀只找黃點。"""
    global _last, _locked, _locked_size, _dot_last, _dot_ts, \
        _purple_prev, _purple_notify_ts
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
            _hist_dot.clear()
        cands = []
        was_unlocked = _locked is None
        if _locked is not None:
            bbox = _locked
        else:
            raw, _d, cands = _detect(frame)
            bbox = _stable_bbox(raw)
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
        # 鎖定嘗試移到「黃點已算完」之後:鎖定門檻需要窗內黃點紀錄
        if was_unlocked and bbox:
            _hist_dot.append(dot is not None and not stale)
            if _maybe_lock(bbox):
                _locked_size = (w, h)
        # 紫色菱形:出現的瞬間(前一幀無→這一幀有)發 Telegram,冷卻內不重發。
        # 【事件回調不受通知冷卻節流】原本 hook 跟 telegram 綁在同一個 if 裡,等於
        # 「解符文的能力被通知冷卻(預設 120s)綁架」—— 冷卻內出現的符文完全不會觸發解除,
        # 而巡邏兜底看到紫標就停,結果是開著巡邏原地不動。通知要節流,事件不該節流。
        purple = []
        if bbox:
            x, y, bw, bh = bbox
            purple = _purple_marks(frame[y:y + bh, x:x + bw])
            now = time.monotonic()
            if purple and not _purple_prev:      # 上升沿
                if now - _purple_notify_ts >= PURPLE_NOTIFY_COOLDOWN:
                    _purple_notify_ts = now
                    pos = ", ".join(f"({px},{py})" for px, py in purple)
                    notify.telegram(f"🟣 小地圖出現紫色標記 x{len(purple)} 位置 {pos}"
                                    f"（小地圖 {bw}x{bh}）")
                if _event_hook:                  # 紫標出現 → 通知外部(解符文/暫停巡邏)
                    try:
                        _event_hook("purple", purple)
                    except Exception as _e:
                        print(f"[minimap] 事件回調錯誤: {_e}")
            _purple_prev = bool(purple)
        if bbox:
            _last = {"found": True, "locked": _locked is not None,
                     "x": bbox[0], "y": bbox[1], "w": bbox[2], "h": bbox[3],
                     "frame_w": w, "frame_h": h,
                     "dot": {"x": dot[0] - bbox[0], "y": dot[1] - bbox[1]} if dot else None,
                     "dot_stale": stale,
                     "purple": [{"x": px, "y": py} for px, py in purple]}
        else:
            _last = {"found": False, "locked": False, "frame_w": w, "frame_h": h}
        return frame, bbox, dot, cands


# ---------- 背景監看(掛機時前端未開預覽也要能發紫標通知) ----------
def _watch_loop(interval, gen):
    """gen = 這條執行緒的世代號。世代不再是最新的就自己退場 ——
    共用一個 Event 當停止旗標時,stop 後緊接 start 會把旗標清掉,舊執行緒會因此復活,
    變成兩條同時在跑完整小地圖偵測(閒置模式反覆開關就會累積)。世代號讓舊的必定退場。"""
    global _watch_ticks, _watch_thread
    while gen == _watch_gen and not _watch_stop_ev.wait(interval):
        if not _watch_wanted():          # 需求方都走了(或前端沒續約)→ 自己退場
            print("[minimap] 已無監看需求方,執行緒退場")
            # 【必須清掉 _watch_thread】否則有競態:這條執行緒正在結束、但 is_alive()
            # 仍為 True 時若來了新的 watch_acquire,watch_start 會以為「已在跑」直接
            # 返回 → 有需求方卻沒有任何人在偵測,而且不會有任何錯誤訊息。
            # 只有自己還是最新世代才清,免得誤清掉別人剛建立的執行緒。
            with _watch_reason_lock:
                if gen == _watch_gen:
                    _watch_thread = None
            break
        try:
            detect_once()
            _watch_ticks += 1
        except Exception as e:
            print(f"[minimap] 監看偵測錯誤: {e}")
    print(f"[minimap] 背景監看執行緒退場(gen={gen})")


def watch_start(interval=2.0):
    """啟動背景監看執行緒(每 interval 秒偵測一次;已在跑則不動作)。"""
    global _watch_thread, _watch_gen
    if _watch_thread is not None and _watch_thread.is_alive():
        return
    _watch_gen += 1                      # 新世代:先前殘留的執行緒看到就會退場
    _watch_stop_ev.clear()
    _watch_thread = threading.Thread(target=_watch_loop,
                                     args=(interval, _watch_gen), daemon=True)
    _watch_thread.start()
    print(f"[minimap] 背景監看啟動(紫標 Telegram 通知, gen={_watch_gen})")


def watch_stop():
    global _watch_thread, _watch_gen
    if _watch_thread is None:
        return
    _watch_gen += 1                      # 讓現有執行緒的世代失效,不只靠 Event
    _watch_stop_ev.set()
    _watch_thread = None
    print("[minimap] 背景監看停止")


# ---------- 背景監看的「需求方」管理 ----------
# 為什麼要引用計數:背景偵測有多個需求方(閒置掛機、中控頁開著巡邏分頁…),
# 直接呼叫 watch_start/watch_stop 的話,任何一方停掉就會把還需要它的另一方也關掉。
# 改成每個需求方各自 acquire/release,集合空了才真正停。
#
# ttl:中控頁那一方必須帶存活時間 —— 瀏覽器直接關掉或斷線時不會有人來 release,
# 沒有 ttl 就會留下一條每 2 秒抓幀的執行緒永遠跑下去。前端定期續約即可。
_watch_reasons = {}                      # reason -> 到期時間(monotonic);None=不過期
_watch_reason_lock = threading.Lock()
_watch_ticks = 0                         # 背景偵測次數,供中控/診斷確認它真的在跑


def watch_acquire(reason, ttl=None):
    """宣告「我需要背景偵測」。ttl 秒後自動失效(None=直到明確 release)。"""
    with _watch_reason_lock:
        _watch_reasons[reason] = None if ttl is None else time.monotonic() + ttl
    watch_start()


def watch_release(reason):
    """撤銷需求。沒有其他需求方時才真正停掉背景偵測。"""
    with _watch_reason_lock:
        _watch_reasons.pop(reason, None)
        left = list(_watch_reasons)
    if not left:
        watch_stop()
    return left


def _watch_wanted():
    """還有沒有有效的需求方(順手清掉過期的)。"""
    now = time.monotonic()
    with _watch_reason_lock:
        for r, exp in list(_watch_reasons.items()):
            if exp is not None and exp <= now:
                del _watch_reasons[r]
                print(f"[minimap] 監看需求 '{r}' 已過期(前端沒續約)")
        return bool(_watch_reasons)


def watch_status():
    _watch_wanted()
    with _watch_reason_lock:
        return {"running": _watch_thread is not None and _watch_thread.is_alive(),
                "reasons": sorted(_watch_reasons), "ticks": _watch_ticks}


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
            for p in _last.get("purple") or []:      # 紫標:白圈標註
                cv2.circle(crop, (p["x"], p["y"]), 8, (255, 255, 255), 1)
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
