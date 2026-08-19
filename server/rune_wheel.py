# -*- coding: utf-8 -*-
"""旋轉符文(解放輪)判向 —— 純 CV,不訓練模型。

背景(使用者實測,不是推導,見 server/rune.py 呼叫端的旋轉分支註解):
  * 這是符文謎題的新版本(遊戲內稱「解放輪」,與舊符文是同一個東西,只是不同
    時期的稱呼),箭頭【持續旋轉】(週期約 0.9 秒),不再像舊版那樣停在固定
    方向等你判讀。遊戲提示明講:「找尋箭頭晃動的方向並依序輸入方向鍵。」
  * 箭頭持續等速旋轉(實測約 -13°/幀 @30fps,即順時鐘),但每圈都在【同一個
    角度】短暫倒退 2 幀(67ms)再恢復原速 —— 那個角速度反轉的瞬間就是「晃動」,
    晃動當下箭頭指向的角度就是答案(四捨五入到最近的上下左右)。
  * 四支箭頭各自獨立相位,必須分開偵測、分開判斷。
  * 若四支箭頭在同一幀附近【同時】出現「晃動」,那是畫面事件(場景切換、
    閃光)或追蹤錯位造成的,不是真的晃動 —— 整批丟掉(見 solve() 的同步過濾)。

【這個模組只做分析,不抓幀】抓幀交給呼叫端(server/rune.py 的旋轉分支,或
tools/eval_rune_wheel.py)。這樣同一套分析邏輯才能既接真實擷取、也接錄好的
影片離線驗證,不必為了測試另外重刻一份。

角度怎麼算 —— 不用模板比對
--------------------------------------------------------------------------
箭頭外觀固定是【綠尾→黃→紅頭】的漸層(與遊戲裡另一種符文箭頭會循環變色不同,
這是這個新版本的固定配色),這個漸層本身就編碼了指向:
    angle = atan2(紅色像素重心 − 綠色像素重心)
對任意角度都準,而且顏色固定所以任何旋轉角度都適用。舊有的四方向模板判向
(rune_cv._direction/_direction_tpl)只在箭頭停在正方向時準,旋轉中途會亂讀
(使用者實測合成資料:停在正方向 78.1%、旋轉中掉到 41.0%),這裡不走那條路。

HSV 遮罩必須含黃色(使用者已踩過的坑)
--------------------------------------------------------------------------
用 `((H<92)|(H>165)) & (S>90) & (V>95)` 讓紅→黃→綠連成一整塊。若把黃色
(H 約 22~32)排除掉,每支箭頭會被切成「紅頭」「綠尾」兩個獨立連通塊,
紅/綠重心對應到形狀完全不同的兩個小 blob,方向會整個算錯。
"""
import math
import os

import cv2
import numpy as np

# ---------- 顏色遮罩(色度分割,使用者實測值,見檔案開頭說明) ----------
HUE_LOW_MAX = 92         # H < 92  (紅→橙→黃→綠 這一段)
HUE_HIGH_MIN = 165       # H > 165 (色相環另一側的紅/洋紅,同樣算「紅」)
SAT_MIN = 90
VAL_MIN = 95

# 漸層兩端各自的「夠紅」「夠綠」子集(用來取紅心/綠心)。不能用整個遮罩做加權
# 平均 —— 那樣重心永遠落在漸層中段,跟箭頭轉到哪個角度沒有穩定關係。
RED_HUE_MAX = 20          # H<=20 或 H>=HUE_HIGH_MIN 視為「紅」(頭)
GREEN_HUE_MIN = 45        # H>=45(上限已被整體遮罩卡在 <92)視為「綠」(尾)

MIN_MASK_PIXELS = 25      # 遮罩總像素數低於此視為「這裡沒有箭頭」
MIN_TIP_PIXELS = 3        # 紅/綠子集各自至少要有這麼多像素才算數
MIN_CENTROID_DIST = 2.0   # 紅心綠心距離太近,方向不可靠(可能是雜訊誤觸發遮罩)


def angle_of(crop_bgr):
    """crop_bgr 裡箭頭的指向角度。

    回傳角度(度),0=右、90=上、逆時針為正(標準數學座標,不是螢幕座標)。
    讀不出(遮罩太小 / 紅綠子集不足 / 重心太近)回 None。
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (((h < HUE_LOW_MAX) | (h > HUE_HIGH_MIN))
            & (s > SAT_MIN) & (v > VAL_MIN)).astype(np.uint8)
    if int(mask.sum()) < MIN_MASK_PIXELS:
        return None

    # 只取最大連通塊 —— 裁切框邊緣可能帶到雜訊像素(相鄰箭頭溢出、背景高飽和
    # 特效)。箭頭本身在裁切框裡永遠是最大的一塊高飽和色塊。
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    best_i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    blob = lab == best_i

    hh = h[blob]
    ys, xs = np.where(blob)

    red = (hh <= RED_HUE_MAX) | (hh >= HUE_HIGH_MIN)
    # 綠色【必須加上界】。整體遮罩是 (h<92)|(h>165),所以 blob 內含 H 165~180 的
    # 洋紅像素 —— 那是紅色箭頭頭部在高亮度下的色相。少了上界時這些像素會【同時】
    # 被算進 red 和 green,把綠尾重心往紅頭拉,兩心距離縮到 MIN_CENTROID_DIST 以下
    # 而回 None。實測 rune_collect 的 1188 支箭頭有 590 支(50%)因此讀不出角度。
    green = (hh >= GREEN_HUE_MIN) & (hh < HUE_LOW_MAX)
    if int(red.sum()) < MIN_TIP_PIXELS or int(green.sum()) < MIN_TIP_PIXELS:
        return None

    rx, ry = float(xs[red].mean()), float(ys[red].mean())
    gx, gy = float(xs[green].mean()), float(ys[green].mean())
    dx, dy = rx - gx, ry - gy
    if math.hypot(dx, dy) < MIN_CENTROID_DIST:
        return None
    # 影像座標 y 朝下,取 -dy 才是「上=正」的數學座標(0=右、90=上)。
    return math.atan2(-dy, dx) * 180.0 / math.pi % 360.0


# ---------- 角度工具:有號最短旋轉角 / 圓形平均 ----------
def _circ_diff(a, b):
    """a 相對 b 的有號最短旋轉角(度),範圍 (-180, 180]。"""
    return ((a - b + 180.0) % 360.0) - 180.0


def circ_mean(angles):
    """角度序列的圓形平均(度,0~360)。0°/360° 交界不會被拉成 180°
    (例如 350° 與 10° 的平均是 0° 附近,不是算術平均的 180°)。
    全部角度互相抵銷(例如剛好對半分布在圓周兩端)或空輸入時回 None。"""
    if not angles:
        return None
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    if abs(s) < 1e-9 and abs(c) < 1e-9:
        return None
    return math.degrees(math.atan2(s, c)) % 360.0


# ---------- 晃動偵測 ----------
# 晃動的表現:角度在某個方向【倒退】(角速度反轉),持續穩定 2 幀(67ms @30fps)
# 再恢復原速。這裡不寫死「2 幀」——production 路徑用 wgc 全速擷取時,同一段
# 67ms 可能對應到不只 2 個樣本(擷取幀率可能高於遊戲的 30fps);離線驗證影片
# 則是原生 30fps。用「連續反轉的幀數上限」(MAX_RUN)當防線就好,不要求剛好等於
# 某個固定幀數。
NOISE_DEG = 1.5     # 小於這個的角速度差視為雜訊,不算反轉(避免量測抖動誤觸發)
MAX_RUN = 6         # 連續反轉幀數上限,超過視為追蹤跟丟或誤判,不算一次晃動


def find_wobbles(angles, frame_idxs):
    """單支箭頭的晃動事件。

    angles / frame_idxs 等長;angles 裡讀不到的位置可以是 None(那一幀 angle_of
    失敗),會被自動跳過,不會讓整段分析中斷。

    回 [(frame, angle), ...]:frame 是事件代表幀(反轉區段的中間幀,只作標記/
    除錯用),angle 是事件的代表角度(區段涵蓋的角度圓形平均,即晃動當下箭頭
    指向的方向)。"""
    pairs = [(f, a) for f, a in zip(frame_idxs, angles) if a is not None]
    if len(pairs) < 3:
        return []

    deltas = []   # (到達幀, 有號最短旋轉角)
    for (f0, a0), (f1, a1) in zip(pairs, pairs[1:]):
        if f1 - f0 != 1:          # 中間缺幀,角速度算出來沒有意義,跳過
            continue
        deltas.append((f1, _circ_diff(a1, a0)))
    if len(deltas) < 3:
        return []

    nz = [d for _f, d in deltas if abs(d) > NOISE_DEG]
    if not nz:
        return []
    # 整體旋轉方向:非雜訊 delta 裡多數的正負號(使用者實測是負的,但這裡不寫死
    # 符號,免得換一個順時針/逆時針的符文就整組失效)。
    n_pos = sum(1 for d in nz if d > 0)
    n_neg = sum(1 for d in nz if d < 0)
    dom_sign = 1.0 if n_pos >= n_neg else -1.0

    runs = []
    run = []
    for f, d in deltas:
        if d * dom_sign < -NOISE_DEG:      # 角速度反了號 → 正在倒退
            run.append(f)
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)

    angle_by_frame = dict(zip(frame_idxs, angles))
    out = []
    for run in runs:
        if len(run) > MAX_RUN:
            continue                        # 反轉太久,不像真的晃動,跳過
        # run 記的是「到達幀」的角速度反轉;連同反轉開始前一幀一起取角度做圓形
        # 平均,代表這次晃動當下箭頭實際指向的方向。
        frs = [run[0] - 1] + run
        angs = [angle_by_frame.get(fr) for fr in frs]
        angs = [a for a in angs if a is not None]
        if not angs:
            continue
        mean_a = circ_mean(angs)
        if mean_a is None:
            continue
        mid_f = run[len(run) // 2]
        out.append((mid_f, mean_a))
    return out


# ---------- 同步假影過濾 + 跨圈多數決 ----------
CARDINALS = {"right": 0.0, "up": 90.0, "left": 180.0, "down": 270.0}
SYNC_TOL_FRAMES = 1     # 判定「同一幀附近」的容差


def nearest_cardinal(angle):
    """角度四捨五入到最近的上下左右(見 CARDINALS)。"""
    best_d, best_name = 1e9, None
    for name, a in CARDINALS.items():
        d = abs(_circ_diff(angle, a))
        if d < best_d:
            best_d, best_name = d, name
    return best_name


def drop_synced_wobbles(per_arrow_wobbles):
    """四支箭頭裡,若某個晃動事件在【其餘三支都有】同一幀附近的晃動事件,判定
    是同步假影(畫面事件/追蹤錯位),整批丟掉那個事件(逐一判斷,不是整支丟)。

    per_arrow_wobbles:長度 4 的 list,每項是該支箭頭的 [(frame, angle), ...]
    (find_wobbles 的回傳格式)。回結構相同、已濾掉假影事件的新 list。
    獨立成公開函式是因為 solve() 與 tools/eval_rune_wheel.py(要分別報告「濾掉
    幾次」)都要用到同一份邏輯,也方便單元測試直接餵合成的晃動事件驗證。"""
    n = len(per_arrow_wobbles)
    all_frames = [[f for f, _a in w] for w in per_arrow_wobbles]
    out = [[] for _ in range(n)]
    for i, wobbles in enumerate(per_arrow_wobbles):
        for f, a in wobbles:
            synced_with = 0
            for j in range(n):
                if j == i:
                    continue
                if any(abs(f - f2) <= SYNC_TOL_FRAMES for f2 in all_frames[j]):
                    synced_with += 1
            if synced_with >= n - 1:      # 其餘全部箭頭都在附近晃 → 假影
                continue
            out[i].append((f, a))
    return out


# ---------- 靜止 / 旋轉分類(逐支獨立判斷) ----------
# 【同一顆符文可能同時有幾支箭頭靜止、幾支在轉】不能假設四支同進退,所以這裡
# 對每一支箭頭【各自】判斷是靜止還是旋轉,不做「整顆符文是不是旋轉款」這種
# 全域分類——四支各自跑同一套流程,自然就同時支援全靜止/全旋轉/混合三種情況。
#
# 判斷方式:算這支箭頭全部有效角度樣本的【圓形集中度】R(0~1)。
#   R 接近 1  = 所有角度幾乎重合在同一點(只有量測抖動)→ 靜止
#   R 接近 0  = 角度均勻散布整個圓 → 在連拍視窗內轉了不只一圈 → 旋轉
# 旋轉週期約 0.9 秒,只要連拍視窗夠長(production 是 3 秒,涵蓋約 3 圈),角度
# 序列一定會繞滿一整圈甚至數圈,R 會被壓得很低;靜止的箭頭就算有量測抖動,
# R 通常還是 >0.9。兩者中間有很寬的間隔,STATIC_R_MIN=0.5 不是精調出來的。
STATIC_R_MIN = 0.5
STATIC_MIN_SAMPLES = 3   # 有效角度樣本數低於此,無法判斷靜止/旋轉,直接算失敗


def _circular_r(angles):
    """圓形集中度(0~1)。"""
    if not angles:
        return 0.0
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.hypot(s, c) / len(angles)


def solve_from_angles(per_arrow_angles, frame_idxs):
    """solve() 的核心邏輯,輸入換成已經算好的角度序列。

    抽出來獨立成一個函式是因為單元測試要餵合成的角度序列驗證邏輯(反轉偵測 /
    同步假影過濾 / 混合靜止+旋轉),不需要真的合成箭頭圖片跑 angle_of ——
    見 tests/test_rune_wheel.py。

    per_arrow_angles:長度 4 的 list,每項是該支箭頭跨影格的角度序列(可以有
    None,代表那一幀讀不出來)。frame_idxs:與序列等長的幀索引(可以有跳號,
    代表該幀之前已被上層濾掉,不能算角速度)。

    回 4 個方向的 list(左到右),或 None(任一支箭頭判不出來)。"""
    if len(per_arrow_angles) != 4:
        return None

    kinds = []                        # 'static' | 'rotating' | None(樣本不足)
    static_angle = [None] * 4
    per_arrow_wobbles = [[] for _ in range(4)]
    for i, angs in enumerate(per_arrow_angles):
        valid = [a for a in angs if a is not None]
        if len(valid) < STATIC_MIN_SAMPLES:
            kinds.append(None)
            continue
        if _circular_r(valid) >= STATIC_R_MIN:
            kinds.append("static")
            static_angle[i] = circ_mean(valid)
        else:
            kinds.append("rotating")
            per_arrow_wobbles[i] = find_wobbles(angs, frame_idxs)

    # 同步假影過濾只對「判定為旋轉」的箭頭有意義(靜止的箭頭本來就不會有晃動
    # 事件);傳整份 per_arrow_wobbles 進去沒問題,靜止箭頭那項恆是空 list,
    # 空 list 在同步判定裡不會貢獻任何「同一幀附近」的匹配,不影響其他支的結果。
    filtered = drop_synced_wobbles(per_arrow_wobbles)

    dirs = []
    for i in range(4):
        if kinds[i] == "static":
            a = nearest_cardinal(static_angle[i]) if static_angle[i] is not None else None
            if a is None:
                return None
            dirs.append(a)
        elif kinds[i] == "rotating":
            wobbles = filtered[i]
            if not wobbles:
                return None
            mean_a = circ_mean([a for _f, a in wobbles])
            if mean_a is None:
                return None
            dirs.append(nearest_cardinal(mean_a))
        else:
            return None
    return dirs


def solve(frames, boxes4):
    """完整流程:4 支箭頭在連續影格裡的角度 →(逐支獨立)判斷靜止/旋轉 →
    靜止取當下角度、旋轉走晃動偵測(含同步假影過濾)→ 最近的上下左右。

    frames:連續影格 list(BGR ndarray)。
    boxes4:4 支箭頭的框,長度 4 的 (x0,y0,x1,y1) list,由左到右——這段期間
        箭頭只在框內原地旋轉或靜止不動,框位置本身視為不變(呼叫端的定位責任,
        這裡不做追蹤)。

    回 4 個方向的 list(左到右,值域同其餘符文模組:"up"/"down"/"left"/
    "right"),或 None(任一支箭頭判不出來)。"""
    if not frames or not boxes4 or len(boxes4) != 4:
        return None

    frame_idxs = list(range(len(frames)))
    per_arrow_angles = []
    for box in boxes4:
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        angs = []
        for fr in frames:
            crop = fr[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            angs.append(angle_of(crop))
        per_arrow_angles.append(angs)

    return solve_from_angles(per_arrow_angles, frame_idxs)


# ---------- 「這是不是解放輪」判別 ----------
# 【為什麼需要這個】舊符文與解放輪【同時存在】(2026-08-19 實測:21:08 解掉的是舊款
# 「要想解放符文,請按順序輸入方向鍵。」,21:24 遇到的是解放輪「找尋箭頭晃動的方向
# 並依序輸入方向鍵。」),而且兩者的膠囊、觸發鍵、紫標驗證全部一樣,只有箭頭會不會
# 轉不一樣。分不出來的代價很實際:解放輪被當成舊款讀,靜態判向讀到的只是「箭頭轉到
# 那一瞬間剛好指哪」,按下去必錯。
#
# 【為什麼不用「CV 多幀讀不出一致答案」當判別】那是 rune.py::_rotating_signal 原本的
# 做法,只在 1 線失敗的分支才會被問到 —— 但旋轉的箭頭每 0.12 秒轉約 48°,而判向只有
# 90° 一格,五幀裡撞到同一格是常態,1 線因此會【自信地給出錯答案】而根本不進那個分支。
# 實測 2026-08-19 21:24:cv_tries=3 就「兩幀一致」收工,697ms 按下去,錯。
#
# 【為什麼用遊戲自己畫的標題橫幅】解放輪在畫面上緣正中央有「解放輪」橫幅,舊款沒有。
# 這是遊戲自己給的、與我方推論無關的訊號,比任何從畫面反推的啟發式都硬。
# 驗收(真實樣本,模板取自另外 40 張、與評估樣本不重疊):
#   解放輪 260 張(rune_collect/*/full.png):259/260 判到(99.6%)
#   舊符文 385 張(rune_dataset/*.png,全部是紫標消失驗證過的):0/385 誤判
#   分數分布 解放輪 p1=0.567 / 舊符文 max=0.420 → 門檻取 0.50,兩邊都有餘裕
TITLE_TPL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rune_wheel_title.png")
TITLE_MIN_SCORE = 0.50
_title_tpl = None
_title_tpl_loaded = False


def _load_title_tpl():
    global _title_tpl, _title_tpl_loaded
    if not _title_tpl_loaded:
        _title_tpl_loaded = True
        _title_tpl = cv2.imread(TITLE_TPL)
        if _title_tpl is None:
            print(f"[rune_wheel] 找不到 {TITLE_TPL},無法判別解放輪,一律當成舊款符文")
    return _title_tpl


def title_score(frame_bgr):
    """畫面上緣「解放輪」橫幅的比對分數(TM_CCOEFF_NORMED 最大值)。

    只在 rune_cv.search_band 的縱向範圍內找 —— 橫幅與膠囊同屬上緣那一層 UI,
    掃整張只會多引入雜訊。模板缺檔或畫面比模板還小時回 -1.0。
    """
    tpl = _load_title_tpl()
    if tpl is None or frame_bgr is None:
        return -1.0
    import rune_cv          # 延遲匯入:本模組只做分析,不該在載入期綁住 rune_cv
    h = frame_bgr.shape[0]
    y0, y1 = rune_cv.search_band(h)
    band = frame_bgr[y0:y1]
    if band.shape[0] < tpl.shape[0] or band.shape[1] < tpl.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(band, tpl, cv2.TM_CCOEFF_NORMED).max())


def looks_like_wheel(frame_bgr):
    """這一幀是不是解放輪(旋轉款)?回 (bool, 分數)。"""
    s = title_score(frame_bgr)
    return s >= TITLE_MIN_SCORE, s
