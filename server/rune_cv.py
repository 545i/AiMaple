# -*- coding: utf-8 -*-
"""純 CV 符文判向:定位膠囊 → 四等分 → 逐支判方向。

rune.py 的第 1 線就是這裡(`_cv_read`),判不出來才退回 claude CLI。

實測(20 張真實符文圖 / 80 支箭頭,真值人工在 4 倍放大下逐支判讀,全部取自冰原地圖):
    全對 19/20 (95%)   單支 79/80 (98%)   膠囊裁切圖 1.4ms / 完整幀 3.6ms
跨地圖驗證(女皇之路,完全沒參與調校):4/4
負樣本(草地地圖、畫面上沒有謎題):正確回報「找不到膠囊」
對照(同一份真值):
    claude CLI(sonnet)      3.6~11s,速度隨 API 負載變動,逾時是主要失敗原因
    本地 VLM Qwen2.5-VL-3B  全對 3/20 (15%),單支 54/80 (67%),~670ms

--------------------------------------------------------------------------
兩個一定要保留的防呆(都是實測踩出來的)
--------------------------------------------------------------------------
1. 只搜畫面上方那一層。掃整張時,草地地圖的黃綠植被在畫面下三分之一湊出一條 282px
   的水平金線,被當成膠囊還回報了 4 個方向 —— 實戰上會按錯鍵、白燒符文冷卻。
2. 上下邊要【配對】(間距 ≈81、x 起點對齊),不能取「最上面那條」。女皇之路的棕褐色
   岩石平台會產生 333~336px 的金線,取最上緣會鎖到岩石、整個框上移 19px,四格全部
   吃進天空與岩石。

--------------------------------------------------------------------------
為什麼定位膠囊而不是定位箭頭
--------------------------------------------------------------------------
`rune.py:_locate_arrows` 靠高飽和色塊找箭頭,在真實幀上會鎖到公告文字或血條。原因是
膠囊【內部】是半透明疊層,背後景物被混色、色度被壓低;膠囊【外面】沒有這層,冰塊
邊緣的飽和度反而比箭頭還高。所以「找箭頭」比「找膠囊」難得多。

膠囊的金色描邊則是理想錨點:
  * UI 固定色,不像箭頭那樣循環變色(實測箭頭會走完整個彩虹,尖端顏色不固定,
    所以任何「尖端是紅色」之類的色彩規則都會失效)
  * 尺寸是不變量:直邊長 ~329px、膠囊高恆為 81px(20 張 + 完整幀實測,高度 100% 一致)
  * 怪物、冰塊、技能特效都不會產生那麼長的水平金線
  * 只要找到任一條邊就能推出整個膠囊,描邊被怪物遮斷也不怕

--------------------------------------------------------------------------
為什麼用色度距離分割而不是飽和度門檻
--------------------------------------------------------------------------
絕對飽和度門檻掃過所有組合最佳只有 69%:門檻拉高會丟掉飽和度較低的箭頭,拉低就把
冰塊吸進來。改用「與膠囊背景色的 Lab (a,b) 距離」後升到 98% —— 膠囊內部的背景已被
半透明層壓成單一色調,取中位色當參考即可,而且暖色與冷色箭頭一體適用、每張圖自適應。
"""
import os

import cv2
import numpy as np
import paths

# ---------- 膠囊定位 ----------
GOLD_LO = (12, 55, 100)      # HSV 下界(OpenCV H 0-179)。範圍要同時涵蓋 WGC 擷取
GOLD_HI = (42, 255, 255)     # (偏暗,實測 S 62~180 / V 119~238)與螢幕截圖(較亮)
EDGE_W = (318, 342)          # 直邊長度。22 張實測最短的那條恆為 328~331,收緊到 ±12 是
                             # 【必要的】:原本放寬到 (270,390) 時,草地地圖的黃綠植被
                             # 湊出一條 282px 的水平金線,就被當成膠囊,還回報了 4 個
                             # 方向 —— 實戰上會按錯鍵、白燒一次符文冷卻。
EDGE_H_MAX = 8
GAP = (76, 86)               # 上下兩條邊的距離(實測 80~81)。要配對而不是「取最上面那條」:
                             # 女皇之路的棕褐色岩石平台會產生 333~336px 的水平金線,
                             # 只取最上緣會鎖到岩石、整個框上移 19px,四格全部吃進天空。
PAIR_DX_MAX = 14             # 上下兩條邊的 x 起點差(同一個膠囊,實測 0~1)
CAP_W, CAP_H = 329, 81       # 膠囊尺寸不變量

# ---------- 搜尋範圍:把畫面橫切成 LAYERS 層,只搜第 SEARCH_LAYER 層 ----------
# 掃整張畫面是錯的:實測草地地圖的黃綠植被在 y=669~750(800 高的畫面下三分之一)湊出
# 一條水平金線,被當成膠囊還回報了 4 個方向。膠囊只會出現在畫面上方,下面兩層根本
# 不該掃 —— 既省時間,也直接消掉一整類誤判來源。
# 實測膠囊上緣落在畫面高度的 0.266~0.295(3 張完整幀),在第一層(0~0.333)內。
# 搜尋下界要再往下延伸 CAP_H:上緣剛好貼在層底時,下緣才不會被切掉。
LAYERS = 3
SEARCH_LAYER = 1             # 1-based,從上往下數
MIN_FRAME_H = 400            # 低於這個高度視為「已裁切的圖」而非完整遊戲畫面 → 不分層。
                             # 分層是對【整幀】的假設(膠囊在畫面上方);對著別人裁好的
                             # 圖再切三分之一只會把膠囊切掉(實測 832x241 的裁切圖,
                             # 膠囊在 0.44 高度處,套用分層就直接找不到)。

# X 軸同樣要收。原本掃整個畫面寬度,雜訊太多 —— 最明顯的是【小地圖邊框】(寬 165px
# @ x=11),它在多個實戰失敗案例裡就是「最長的金色線段」,把配對搜尋整個帶偏;右側的
# 技能/buff 圖示也會湊出金色橫線。
#
# 膠囊是【固定在視窗上緣中央的 UI】,不隨角色移動(使用者確認)。實測完整幀 6 張:
#   左緣 0.378~0.393　右緣 0.620~0.634   ← 擺動僅 1.5%,正是固定 UI 的特徵
# 左右各切 30% 後搜尋範圍 0.30~0.70,對實測值左右各留約 0.08(1368 幀 ≈ 100px)餘裕,
# 同時把畫面兩側 60% 的雜訊全部排除。
# 與 Y 同理:裁切過的圖不套用(膠囊在使用者裁好的圖裡本來就佔滿寬度)。
SEARCH_X_MARGIN = 0.30
MIN_FRAME_W = 900            # 低於此寬度視為裁切圖 → 不收 X(同 MIN_FRAME_H 的理由)

# 「這真的是謎題膠囊嗎」的驗證。刻意【只用與地圖無關的不變量】:
# 22 張樣本全是同一張冰原地圖,膠囊內部的 H/S/V(實測 H 104~105、V 180~187)雖然分得更
# 開,但那是半透明疊層混到冰原底色的結果,換地圖就會漂移,不能當硬門檻。
# 箭頭 sprite 尺寸則與地圖無關:實測四格面積 394~445,同張圖 max/min 只有 1.00~1.11。
AREA_OK = (250, 650)
AREA_RATIO_MAX = 2.0

# ---------- 箭頭分割 / 判向 ----------
CHROMA_MIN = 20              # 離背景色度的距離門檻。20/25/30 分別為 99/96/95%,
                             # 是個寬的平台,不是調參調出來的
BLOB_MIN = 80
EDGE_BAND = 0.18             # 判向時比較最外緣這個比例的帶狀區域


def imread(path):
    """cv2.imread 在 Windows 讀不了含非 ASCII 字元的路徑(會靜默回 None),故繞道 imdecode。"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _gold_mask(bgr):
    return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), GOLD_LO, GOLD_HI)


def _edges_by_components(m):
    n, _l, st, _c = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        w = int(st[i, cv2.CC_STAT_WIDTH])
        h = int(st[i, cv2.CC_STAT_HEIGHT])
        if EDGE_W[0] <= w <= EDGE_W[1] and h <= EDGE_H_MAX:
            out.append((int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP]), w, h))
    return out


def _edges_by_rows(m):
    """退路:描邊被怪物打斷時連通元件會碎掉,改在每一列找最長的金色連續段。"""
    out = []
    for y in range(m.shape[0]):
        xs = np.where(m[y] > 0)[0]
        if len(xs) < EDGE_W[0] * 0.5:
            continue
        best_len, best_s, s, prev = 0, 0, xs[0], xs[0]
        for x in xs[1:]:
            if x - prev > 12:
                if prev - s > best_len:
                    best_len, best_s = prev - s, s
                s = x
            prev = x
        if prev - s > best_len:
            best_len, best_s = prev - s, s
        if EDGE_W[0] <= best_len <= EDGE_W[1]:
            out.append((int(best_s), y, int(best_len), 1))
    return out


def search_band(h):
    """回 (y0, y1):只在這個縱向範圍內找膠囊。下界多留 CAP_H,避免上緣貼在層底時
    膠囊本體被切掉。畫面太小(已裁切的圖)則不分層,整張都找。"""
    if h < MIN_FRAME_H:
        return 0, h
    y0 = int(h * (SEARCH_LAYER - 1) / LAYERS)
    y1 = int(h * SEARCH_LAYER / LAYERS) + CAP_H
    return max(0, y0), min(h, y1)


def search_x(w):
    """回 (x0, x1):只在這個橫向範圍內找膠囊。膠囊是固定在視窗上緣中央的 UI,
    掃全寬只會把小地圖邊框、技能圖示等雜訊收進候選。裁切過的圖不套用。"""
    if w < MIN_FRAME_W:
        return 0, w
    return int(w * SEARCH_X_MARGIN), int(w * (1.0 - SEARCH_X_MARGIN))


def find_capsule(bgr):
    """回膠囊框 (x0, y0, x1, y1),找不到回 None。

    搜尋範圍由 search_band(縱向) + search_x(橫向) 界定 —— 膠囊固定在視窗上緣中央,
    掃整張畫面只會多引入誤判來源(實測:草地植被的假金線、小地圖邊框)。

    寬度一律鎖成 CAP_W:實測會量到 352~363(描邊與相鄰的黃色箭頭像素連通),不修正的話
    第 1 格會越過左端蓋、吃進膠囊外的背景,分割就會壞掉。"""
    by0, by1 = search_band(bgr.shape[0])
    bx0, bx1 = search_x(bgr.shape[1])
    if by1 <= by0 or bx1 <= bx0:
        return None
    m = _gold_mask(bgr[by0:by1, bx0:bx1])
    segs = _edges_by_components(m) or _edges_by_rows(m)
    if not segs:
        return None

    # 先找【配對】:上下兩條邊間距 ≈CAP_H、x 起點對齊。單看「最上面那條」會被場景裡
    # 其他長金線騙走(岩石平台、木質橫樑…),那是實測踩過的坑。
    best = None
    for i, a in enumerate(segs):
        for b in segs[i + 1:]:
            gap = abs(b[1] - a[1])
            if not (GAP[0] <= gap <= GAP[1]) or abs(a[0] - b[0]) > PAIR_DX_MAX:
                continue
            score = abs(gap - CAP_H) + abs(a[2] - CAP_W) + abs(b[2] - CAP_W)
            if best is None or score < best[0]:
                best = (score, min(a[1], b[1]), min(a[0], b[0]))
    if best is not None:
        ytop, x0 = best[1], best[2]
    else:
        # 退路:截圖把一條邊切到畫面外時只會有單邊(使用者手動裁的圖會這樣,
        # 完整幀不該走到這裡)。
        segs.sort(key=lambda s: abs(s[2] - CAP_W))
        x0, y = segs[0][0], segs[0][1]
        ytop = y if y < (by1 - by0) / 2 else y - CAP_H

    # 換回整幀座標:搜尋是在 bgr[by0:by1, bx0:bx1] 上做的,x 也要加回偏移
    ytop = max(0, min(by0 + ytop, bgr.shape[0] - 1))
    x0 = max(0, min(bx0 + x0, bgr.shape[1] - 1))
    return (x0, ytop, min(bgr.shape[1], x0 + CAP_W),
            min(bgr.shape[0], ytop + CAP_H))


def slots(box, n=4):
    """膠囊框等分成 n 格,每格正好框住一支箭頭(20 張 80 格實測對位皆正確)。"""
    x0, y0, x1, y1 = box
    w = (x1 - x0) / n
    return [(int(x0 + k * w), y0, int(x0 + (k + 1) * w), y1) for k in range(n)]


def _chroma_map(cap_bgr):
    """膠囊內每個像素離「背景色度」的距離。背景佔多數 → 中位色即背景。"""
    lab = cv2.cvtColor(cap_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    a, b = lab[:, :, 1], lab[:, :, 2]
    return np.hypot(a - np.median(a), b - np.median(b))


def _seg(dist, x0, x1):
    sub = dist[:, x0:x1]
    h, w = sub.shape
    inner = np.zeros_like(sub, bool)
    inner[int(h * 0.10):int(h * 0.90), int(w * 0.08):int(w * 0.92)] = True
    mask = ((sub >= CHROMA_MIN) & inner).astype(np.uint8) * 255
    k = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k)
    n, lab, st, cent = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    best, bs = None, -1e9
    for i in range(1, n):
        area = int(st[i, cv2.CC_STAT_AREA])
        if area < BLOB_MIN:
            continue
        d = np.hypot(cent[i][0] - w / 2, cent[i][1] - h / 2)
        score = area - d * 20          # 面積大且靠格子中心者優先
        if score > bs:
            bs, best = score, i
    return None if best is None else (lab == best).astype(np.uint8) * 255


# 四旋轉模板:100 支已標註箭頭依標籤轉回同一朝向後平均而成(576 bytes)。
# 為什麼要它:邊帶啟發式只比較四條邊帶的像素數比值再取大者,【沒有要求差距】,
# 遇到上下倒鉤讓外框比寬還高的箭頭時,垂直方向的雜訊比值會壓過水平方向而翻掉
# (實測 20260727-013514-311 第 4 格,分割完全正確卻把 right 判成 up)。
# 模板比對用整個形狀,留一張圖交叉驗證 100/100,邊帶啟發式 99/100。
_TPL_PATH = paths.srv_res("rune_arrow_tpl.png")  # 唯讀資源:內嵌在 exe
_TPL_N = 32
_TPL_DIRS = ["up", "right", "down", "left"]      # 順時針,對應 rot90 次數
_TPL = None
if os.path.exists(_TPL_PATH):
    _t = cv2.imread(_TPL_PATH, cv2.IMREAD_GRAYSCALE)
    if _t is not None and _t.shape == (_TPL_N, _TPL_N):
        _TPL = _t.astype(np.float32) / 255.0


def _direction_tpl(mask):
    """用四旋轉模板判向。模板不在就回 None,由呼叫端退回啟發式。"""
    if _TPL is None:
        return None
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    nm = (cv2.resize(sub, (_TPL_N, _TPL_N),
                     interpolation=cv2.INTER_AREA) > 127).astype(np.float32)
    best, bs = None, -1e9
    for d in _TPL_DIRS:
        t = np.rot90(_TPL, -_TPL_DIRS.index(d))
        score = float((nm * t).sum() - ((1 - nm) * t).sum())
        if score > bs:
            bs, best = score, d
    return best


def _direction(mask):
    """箭頭 = 三角頭 + 方尾。尖端那側會收斂成一點,尾側是平的方邊,
    所以比較四個方向最外緣帶狀區的像素數,最少的那側就是尖端。

    這是模板不可用時的退路 —— 它沒有差距要求,形狀略不對稱時會翻掉。"""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    bx = max(2, int((x1 - x0 + 1) * EDGE_BAND))
    by = max(2, int((y1 - y0 + 1) * EDGE_BAND))
    left = int((xs < x0 + bx).sum())
    right = int((xs > x1 - bx).sum())
    top = int((ys < y0 + by).sum())
    bot = int((ys > y1 - by).sum())
    h_diff = abs(left - right) / max(1, left + right)
    v_diff = abs(top - bot) / max(1, top + bot)
    if h_diff >= v_diff:
        return "right" if right < left else "left"
    return "down" if bot < top else "up"


def read_dirs(frame_bgr, strict=True):
    """從一張遊戲畫面讀出 4 支箭頭方向。

    回 (dirs, err)。dirs 是長度 4 的 list,讀不到的位置是 None;判定不是膠囊時回 []。

    strict=True 會做「這真的是膠囊嗎」的驗證。**不要為了提高偵測率把它關掉**:
    誤判的代價不是漏一次,而是拿背景雜訊當箭頭去按方向鍵,白燒一次符文冷卻。
    預覽用 strict=False 才看得到「抓到什麼」以便診斷。"""
    box = find_capsule(frame_bgr)
    if box is None:
        return [], "找不到謎題膠囊(還沒開謎題?)"
    cap = frame_bgr[box[1]:box[3], box[0]:box[2]]
    if cap.size == 0:
        return [], "膠囊區域為空"
    dist = _chroma_map(cap)
    w = cap.shape[1] / 4
    dirs, areas = [], []
    for k in range(4):
        m = _seg(dist, int(k * w), int((k + 1) * w))
        if m is None:
            dirs.append(None)
            areas.append(0)
            continue
        dirs.append(_direction_tpl(m) or _direction(m))
        areas.append(int((m > 0).sum()))
    if strict:
        bad = [a for a in areas if not (AREA_OK[0] <= a <= AREA_OK[1])]
        ratio = max(areas) / max(1, min(areas))
        if bad or ratio > AREA_RATIO_MAX:
            return [], (f"不像謎題膠囊(四格面積 {areas},比值 {ratio:.1f})")
    miss = sum(1 for d in dirs if d is None)
    return dirs, "" if miss == 0 else f"{miss} 支讀不出來"


_ARROW_GLYPH = {"up": "^", "down": "v", "left": "<", "right": ">"}


def preview(frame_bgr, scale=3):
    """回一張標註後的膠囊預覽圖(BGR),供中控頁確認定位與判讀是否正確。

    找不到膠囊時回整幀縮圖 + 提示 —— 回 None 的話前端只會看到破圖,
    分不出「沒開謎題」與「定位壞了」。"""
    box = find_capsule(frame_bgr)
    if box is None:
        h, w = frame_bgr.shape[:2]
        k = 480 / max(1, w)
        small = cv2.resize(frame_bgr, (int(w * k), int(h * k)))
        cv2.putText(small, "no capsule", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (60, 60, 240), 2)
        return small

    pad = 6
    x0 = max(0, box[0] - pad)
    y0 = max(0, box[1] - pad)
    cap = frame_bgr[y0:min(frame_bgr.shape[0], box[3] + pad),
                    x0:min(frame_bgr.shape[1], box[2] + pad)]
    img = cv2.resize(cap, (cap.shape[1] * scale, cap.shape[0] * scale),
                     interpolation=cv2.INTER_NEAREST)
    # 標籤另開一條下方色帶,不疊在影像上 —— 疊上去會被膠囊下緣切掉,而且蓋住箭頭
    BAND = 34
    vis = np.zeros((img.shape[0] + BAND, img.shape[1], 3), np.uint8)
    vis[:img.shape[0]] = img
    # 預覽同時顯示嚴格判定的結果與寬鬆讀到的內容:被驗證擋下時仍畫出方向,
    # 才看得出來「擋掉的是誤判還是誤殺」。
    strict_dirs, verdict = read_dirs(frame_bgr, strict=True)
    dirs, _ = read_dirs(frame_bgr, strict=False)
    if verdict:
        cv2.putText(vis, verdict[:46], (6, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (60, 60, 240), 2)
    cv2.rectangle(vis, (pad * scale, (box[1] - y0) * scale),
                  ((box[2] - x0) * scale, (box[3] - y0) * scale), (0, 200, 255), 2)
    sw = (box[2] - box[0]) / 4
    for k in range(4):
        sx = int((box[0] - x0 + k * sw) * scale)
        ex = int((box[0] - x0 + (k + 1) * sw) * scale)
        if k:
            cv2.line(vis, (sx, (box[1] - y0) * scale), (sx, (box[3] - y0) * scale),
                     (0, 200, 255), 1)
        d = dirs[k] if k < len(dirs) else None
        # 綠=採用;黃=讀到但被驗證擋下(疑似誤判);紅=讀不出來
        col = (80, 240, 120) if strict_dirs else ((60, 200, 240) if d else (60, 60, 240))
        cv2.putText(vis, _ARROW_GLYPH.get(d, "?"),
                    (sx + (ex - sx) // 2 - 9, img.shape[0] + BAND - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
    return vis


def preview_jpeg(frame_bgr, scale=3, quality=85):
    """preview() 的 JPEG 位元組版本,給 HTTP 端點用。失敗回 None。"""
    vis = preview(frame_bgr, scale)
    ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None


DATASET_DIR = paths.data_dir("rune_dataset")     # 可寫:必須在素材夾,不能在暫存解壓目錄

# 評測用真值:20 張真實符文圖(檔名為 OneDrive\图片\Screenshots 下的截圖,依時間排序)。
# 【已被 rune_dataset/index.jsonl 取代】,這份留著只是紀錄最初那批的判讀結果。
# 注意:初版真值有 11 處錯誤,全部發生在水平箭頭 —— 放大倍率不足時容易把箭尾的方塊
# 誤認成箭頭。此為 4 倍放大重判後的版本。
TEST_TRUTH = [
    ["up", "down", "right", "left"], ["right", "down", "down", "up"],
    ["down", "right", "left", "down"], ["down", "left", "up", "down"],
    ["left", "down", "right", "up"], ["down", "up", "up", "down"],
    ["right", "up", "right", "right"], ["left", "down", "down", "left"],
    ["left", "right", "left", "up"], ["up", "left", "up", "right"],
    ["down", "up", "left", "up"], ["left", "up", "left", "left"],
    ["up", "left", "left", "down"], ["right", "right", "down", "left"],
    ["left", "up", "left", "down"], ["left", "up", "down", "right"],
    ["down", "left", "up", "left"], ["up", "up", "up", "down"],
    ["up", "left", "right", "down"], ["right", "right", "right", "right"],
]


def evaluate(img_dir=None):
    """跑評測。預設讀 `rune_dataset/index.jsonl`(圖與標籤放在一起,自足且可重現)。

    也接受一個資料夾:那是舊的權宜作法 —— 靠「檔名排序的第 N 張對應 TEST_TRUTH[N]」,
    只要那個資料夾增刪檔案就會默默對錯行、給出無意義的數字卻不報錯。有索引就別用它。"""
    import glob
    import json as _json

    ds = img_dir or DATASET_DIR
    idx = os.path.join(ds, "index.jsonl")
    if os.path.exists(idx):
        recs = [_json.loads(s) for s in open(idx, encoding="utf-8") if s.strip()]
        exact = ok = tot = neg_n = neg_ok = 0
        for r in recs:
            img = imread(os.path.join(ds, r["file"]))
            if img is None:
                continue
            dirs, err = read_dirs(img)
            if r.get("negative"):
                neg_n += 1
                neg_ok += len(dirs) == 0
                print(f"{r['file']}  [負] 期望無讀值 -> {dirs} "
                      f"{'OK' if not dirs else 'FAIL'}")
                continue
            truth = r["dirs"]
            if len(dirs) == 4:
                ok += sum(a == b for a, b in zip(dirs, truth))
                exact += dirs == truth
            tot += 4
            print(f"{r['file']}  {truth} -> {dirs}")
        n = tot // 4
        print(f"\n正樣本 全對 {exact}/{n}   單支 {ok}/{tot}")
        if neg_n:
            print(f"負樣本 通過 {neg_ok}/{neg_n}")
        return exact, ok, tot

    files = sorted(f for f in glob.glob(os.path.join(ds, "*.png"))
                   if os.path.getsize(f) > 10000)[:len(TEST_TRUTH)]
    print(f"(無索引,退回檔名排序對應 TEST_TRUTH —— 資料夾內容一變就會對錯行)")
    exact = ok = tot = 0
    for i, f in enumerate(files):
        img = imread(f)
        if img is None:
            print(f"讀不到 {f}")
            continue
        dirs, _err = read_dirs(img)
        truth = TEST_TRUTH[i]
        if len(dirs) == 4:
            ok += sum(a == b for a, b in zip(dirs, truth))
            exact += dirs == truth
        tot += 4
        print(f"{os.path.basename(f)}  {truth} -> {dirs}")
    print(f"\n全對 {exact}/{len(files)}   單支 {ok}/{tot}")
    return exact, ok, tot


if __name__ == "__main__":
    import sys
    evaluate(sys.argv[1] if len(sys.argv) > 1 else None)
