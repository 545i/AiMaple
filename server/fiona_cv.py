# -*- coding: utf-8 -*-
"""菲歐娜解謎(遊戲內名稱「謊言探測儀」)的純 CV 元件。

--------------------------------------------------------------------------
遊戲玩法
--------------------------------------------------------------------------
題目:「點擊按鈕,選擇化妝的碧歐蕾塔目前所在的位置吧!」
  1. 展示階段:紅簾幕白背景,四隻蘑菇【清楚露臉不透明】,遊戲用【橘色箭頭】
     直接指出目標。這是全場唯一能辨認目標的時刻。
  2. 洗牌階段:燈暗,四隻變半透明、在四個固定聚光燈位之間移動交換,會轉身、
     會互相遮擋,而且【四隻外觀完全相同】。
  3. 作答:四顆按鈕變藍 + 4 秒倒數,點對應數字。一場 4 輪,錯一輪就受懲罰。

--------------------------------------------------------------------------
為什麼是純 CV 而不是偵測模型
--------------------------------------------------------------------------
試過 Roboflow 現成資料集(1-gnqic/bvhjcnkjxn-mwdfodsnvjkscx-mvnwoc v6,6595 張)
訓 YOLOv11n:資料集 val 的 mAP50 有 0.995,但那是假的 —— train/valid/test 幾乎
是同一批原圖(train∩valid=165/170、train∩test=184/186,全部來自同一段影片的
第 134~584 幀),等於在訓練集上評估。而且標註不完整(四隻都露臉卻只框一隻),
模型忠實學會了漏檢。

實機影片上量【production 真正需要的指標】——「恰好 4 個框且分屬 4 個不同槽」——
只有 29.6%(595 個視窗開啟幀:0框28/1框112/2框81/3框105/4框210/5框52/6框7)。

改走純 CV 後,同樣的實機影片上單輪正確率 11/12。這與符文判向的結論一致
(見 MEMORY:符文判向已用純 CV 解決,98% 單支、數毫秒)。

--------------------------------------------------------------------------
為什麼追蹤用 Viterbi 而不是卡爾曼 + 匈牙利
--------------------------------------------------------------------------
卡爾曼 + 匈牙利是【因果且貪婪】的:每一幀只用過去資訊做一次不可撤回的配對。
四隻外觀完全相同,交錯瞬間的資訊量本來就不足以做對決策,一旦配錯,軌跡就換人,
而且後面所有幀都會自信地繼續追錯的那隻。

但這個場景【根本不需要因果】:答案是作答倒數時才要交出來的,那時整輪洗牌已經
全部發生完了。Viterbi 在整段時間上求全域最優,交錯那一刻不必當場決定,而是看
「之後那兩條軌跡各自往哪走」反推當時該怎麼配對 —— 把決策延後到資訊充足時再做。

實測支撐:稠密光流量到每幀位移中位數 1.4~1.5px、p99 5.4px(24fps),而蘑菇寬度
約等於單槽寬 103px。位移只有物體寬度的 1.5%,運動極平滑,所以轉移約束取 8px
已有 1.5 倍餘裕。

--------------------------------------------------------------------------
真值一律來自計分格,不從模型輸出
--------------------------------------------------------------------------
計分格是遊戲自己畫在畫面上的:答對填黃色數字,整場失敗時轉紅並歸零(0000)。
這條規矩來自本專案踩過的坑(見 server/rune_collect.py):拿判向器自己的輸出當
標籤,等於把錯誤固化進資料集。
"""
import os

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 版面座標
# 全部是【相對視窗左上角】的偏移。實測視窗位置會變(同一段影片裡量到 (469,250)
# 283 次、(380,73) 4 次、(381,303) 5 次…),所以絕不能寫死絕對座標。
STAGE = (12, 61, 425, 254)          # 舞台區(聚光燈 + 蘑菇 + 計分格)
BAND = (12, 150, 425, 254)          # 蘑菇所在的水平帶;由運動能量 y 剖面量出
                                    # (能量 >0.35 落在全幀 y 401..504,峰值 452)
SCORE_CELLS = [(178, 69, 195, 89), (199, 69, 216, 89),
               (221, 69, 238, 89), (242, 69, 259, 89)]
BUTTONS = [(12, 260, 110, 297), (116, 260, 215, 297),
           (221, 260, 319, 297), (324, 260, 423, 297)]
N_SLOTS = 4

SEARCH_MAX_W = 900                  # 多尺度搜尋前先把幀縮到這個寬度以內
WINDOW_MATCH_MIN = 0.80             # 按鈕列模板比對門檻。
# 實測【真命中】四個語言版本都在 0.92~0.99(繁中 0.923、韓文 0.994、英文 0.922、
# 簡中 1.000);而錯誤 scale 造成的【假命中】約 0.75。門檻取 0.85 落在兩者之間。
# 舊值 0.75 太鬆:曾讓搜尋在錯誤的 scale 上提早收工(0.752 @ scale 0.96)。
TITLE_MATCH_MIN = WINDOW_MATCH_MIN  # 舊名保留,避免呼叫端一次改太多
BUTTON_LIT_MIN = 0.35               # 按鈕變藍的面積比門檻;實測 p50=0.00/p90=0.72
MAX_STEP_PX = 8                     # Viterbi 每幀最大位移;實測 p99=5.4px @24fps
SMOOTH_LAMBDA = 0.02                # 平滑懲罰(位移的線性成本)
# 【這個值刻意保守,而且沒有證據支持它比 0 更好】。
# 一度以為 0.10 是甜蜜點(12 輪全對),但那是 viterbi_track 的遞推 bug 造成的
# 假象 —— 平滑懲罰原本在選完最小值之後才加,等於沒有參與轉移選擇。修掉之後
# 重掃(同樣 12 輪):
#     0.00→11/12  0.02→11/12  0.05→11/12  0.10→11/12(錯的換了一輪)
#     0.20→10/12  0.40→6/12   0.80→1/12
# 0~0.05 完全等價,再大只會更差。取 0.02 是「留一點抗噪餘裕但幾乎不偏置軌跡」,
# 不是因為它比 0 好。等實機資料累積夠了再定案。


def _load_gray(name):
    p = os.path.join(_HERE, name)
    im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise FileNotFoundError(p)
    return im


def _load_color(name):
    p = os.path.join(_HERE, name)
    im = cv2.imread(p, cv2.IMREAD_COLOR)
    if im is None:
        raise FileNotFoundError(p)
    return im


_TITLE_TPL = None
_DIGIT_TPL = None
_BTN_TPL = None

# 按鈕列模板在【視窗座標系(scale=1)】的原點,用來從模板命中位置反推視窗左上角。
BUTTONS_TPL_ORIGIN = (12, 258)
# 多尺度搜尋範圍。UI 是等比縮放,比例 = 遊戲畫面寬 / 1370(模板來源的寬度):
#     1280 寬 → 0.94   1360 寬 → 1.00   1920 寬 → 1.40
# 實測 scale 很敏感(0.94→0.923、0.92→0.878、0.96→0.802),所以格點取 0.02。
SCALE_MIN, SCALE_MAX, SCALE_STEP = 0.60, 1.60, 0.02


def _title_tpl():
    global _TITLE_TPL
    if _TITLE_TPL is None:
        _TITLE_TPL = _load_color("fiona_title_tpl.png")
    return _TITLE_TPL


def _btn_tpls():
    """按鈕列模板,兩種狀態都要。

    【為什麼需要兩個】作答窗口時四顆按鈕會變藍,灰階外觀跟平常差很多,只用
    「未亮起」那個模板會在作答那一刻定位失敗(實測韓文版某幀 score 掉到 0.559)。
    而作答正是最關鍵的時刻 —— 視窗絕不能在那時「消失」。
    """
    global _BTN_TPL
    if _BTN_TPL is None:
        _BTN_TPL = [_load_gray("fiona_buttons_tpl.png"),
                    _load_gray("fiona_buttons_lit_tpl.png")]
    return _BTN_TPL


def _btn_tpl():
    """預設(未亮起)的按鈕列模板,只給尺寸換算用。"""
    return _btn_tpls()[0]


def scale_of(frame_width):
    """從遊戲畫面寬度推 UI 縮放比例。多尺度搜尋的起點,不是最終答案。"""
    return float(frame_width) / 1370.0


def sbox(box, scale):
    """把 scale=1 定義的相對座標框換算到實際縮放。"""
    if scale == 1.0:
        return box
    return tuple(int(round(v * scale)) for v in box)


def _digit_tpl():
    global _DIGIT_TPL
    if _DIGIT_TPL is None:
        _DIGIT_TPL = {d: (_load_gray("fiona_digit_" + d + ".png") > 0)
                      for d in "01234"}
    return _DIGIT_TPL


# ---------------------------------------------------------------- 視窗定位
SEARCH_MARGIN = 48                  # hint 命中時的局部搜尋半徑(px)


def _match_btn(img_gray, scale):
    """在灰階幀裡找按鈕列,回 (score, 視窗左上角);模板放不進去回 (0, None)。

    未亮起/亮起兩個模板都試,取分數高的 —— 作答時按鈕變藍,外觀差很多。
    先試未亮起那個(絕大多數時間是這個狀態),夠高就不必試第二個。
    """
    best_sc, best_win = 0.0, None
    for i, tpl in enumerate(_btn_tpls()):
        if scale != 1.0:
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            tpl = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=interp)
        th, tw = tpl.shape[:2]
        if img_gray.shape[0] < th or img_gray.shape[1] < tw:
            continue
        res = cv2.matchTemplate(img_gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best_sc:
            ox, oy = BUTTONS_TPL_ORIGIN
            best_sc = float(mx)
            best_win = (int(loc[0] - ox * scale), int(loc[1] - oy * scale))
        if i == 0 and best_sc > 0.92:
            break
    return best_sc, best_win


def find_window(frame, hint=None, scale=None):
    """找謎題視窗,回 ((x, y), score, scale);找不到回 (None, score, None)。

    【為什麼用底部的 1/2/3/4 按鈕列,不用標題列】標題列文字會隨遊戲語言版本改變
    —— 實測同一個小遊戲在四段錄影裡分別是簡體「谎言探测仪」、繁體「注意千萬
    不要錯過化妝後的菲歐娜喔」、英文「LIE DETECTOR」、韓文「버튼을 눌러…」。
    拿標題列當模板只對開發時那個版本有效(其他三個版本分數 0.64~0.69,全部落在
    門檻之下)。阿拉伯數字 1/2/3/4 則在所有版本一致,實測四個版本都能到 0.92~0.99。

    【多尺度】UI 會等比縮放,比例 = 畫面寬 / 1370。1280 寬→0.94、1360→1.00、
    1920→1.40,實測與理論值吻合。scale 給定就只試那一個(快);沒給就從畫面寬度
    推一個起點,由近而遠掃描 —— 起點通常就是答案,所以絕大多數情況第一次就中。

    【hint 是效能關鍵】全圖比對實測 48.6ms/幀只夠 20fps,實機要 60fps。給上一幀
    的位置當 hint 只在附近搜尋(2.7ms);沒命中才退回全圖,視窗被拖動時仍找得回來。
    """
    if frame is None or frame.size == 0:
        return None, 0.0, None
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

    if hint is not None and scale is not None:
        tpl_w = int(round(_btn_tpl().shape[1] * scale))
        tpl_h = int(round(_btn_tpl().shape[0] * scale))
        ox, oy = BUTTONS_TPL_ORIGIN
        bx = int(hint[0] + ox * scale)
        by = int(hint[1] + oy * scale)
        x0, y0 = max(0, bx - SEARCH_MARGIN), max(0, by - SEARCH_MARGIN)
        x1 = min(g.shape[1], bx + tpl_w + SEARCH_MARGIN)
        y1 = min(g.shape[0], by + tpl_h + SEARCH_MARGIN)
        sub = g[y0:y1, x0:x1]
        if sub.shape[0] >= tpl_h and sub.shape[1] >= tpl_w:
            sc, w = _match_btn(sub, scale)
            if sc > TITLE_MATCH_MIN and w is not None:
                return (w[0] + x0, w[1] + y0), sc, scale

    if scale is not None:
        sc, w = _match_btn(g, scale)
        if sc > TITLE_MATCH_MIN:
            return w, sc, scale
        return None, sc, None

    # 【必須掃完取全域最佳,不能一過門檻就收工】。scale 猜錯時會先撞到次佳解:
    # 實測拿裁切圖(scale_of 猜 0.46,實際 1.0)時,搜尋在 scale=0.96 撞到 0.752
    # 就返回,視窗座標整個偏掉。
    #
    # 【coarse-to-fine】掃 50 個 scale 的全圖比對在 1920x1080 上要 1451ms,
    # 視窗沒開時反覆重試會直接拖死(實測單支 2110 幀的影片要 383 秒)。所以先把
    # 幀縮到 SEARCH_MAX_W 以內掃出粗略 scale(成本降到平方分之一),再回原圖用
    # 該 scale 附近精修。
    k = min(1.0, SEARCH_MAX_W / float(g.shape[1]))
    gs = g if k == 1.0 else cv2.resize(g, None, fx=k, fy=k,
                                       interpolation=cv2.INTER_AREA)
    guess = scale_of(frame.shape[1]) * k
    cand = sorted(np.arange(SCALE_MIN * k, SCALE_MAX * k + 1e-9, SCALE_STEP * k),
                  key=lambda s: abs(s - guess))
    coarse_sc, coarse_s = 0.0, None
    for s in cand:
        sc, _w = _match_btn(gs, round(float(s), 3))
        if sc > coarse_sc:
            coarse_sc, coarse_s = sc, round(float(s), 3)
    if coarse_s is None:
        return None, coarse_sc, None

    # 粗掃的 scale 是在縮圖上的,換回原圖尺度後在附近精修。
    # 【精修步進必須比粗掃細】實測 1920x1080 那支的真實 scale 是 1.41,而粗掃
    # 格點 0.02 只會給 1.40 或 1.42 —— 差這 0.01 就讓分數從 0.83~0.95 掉到
    # 0.79~0.92,剛好卡在門檻上下。所以精修用 0.01 掃 ±0.03。
    base = coarse_s / k
    best_sc, best_w, best_s = 0.0, None, None
    for d in np.arange(-0.03, 0.03 + 1e-9, 0.01):
        s = round(base + float(d), 3)
        if s <= 0:
            continue
        sc, w = _match_btn(g, s)
        if sc > best_sc:
            best_sc, best_w, best_s = sc, w, s
    if best_sc > WINDOW_MATCH_MIN:
        return best_w, best_sc, best_s
    return None, best_sc, None


def crop(frame, win, box, scale=1.0):
    """依【相對視窗】的 box 裁圖;超出邊界回 None(視窗被推出畫面時會發生)。

    box 一律用 scale=1(1370 寬)的座標寫死,實際縮放在這裡換算,所以呼叫端不必
    自己乘來乘去。
    """
    wx, wy = win
    x1, y1, x2, y2 = sbox(box, scale)
    if wy + y1 < 0 or wx + x1 < 0:
        return None
    c = frame[wy + y1:wy + y2, wx + x1:wx + x2]
    if c.shape[0] != y2 - y1 or c.shape[1] != x2 - x1:
        return None
    return c


# ---------------------------------------------------------------- 槽位校準
def slot_centers(frame, win, scale=1.0):
    """回四個槽的中心 x(相對舞台區左緣)。抓不到 4 個峰時回等分位置。

    聚光燈【固定不動】(x-t 圖上是四條完全垂直的線,不隨蘑菇移動),所以拿它當
    槽位標記。取舞台上部(光錐彼此分離處)的亮度剖面找峰值,實測 80/80 幀都
    恰好 4 個峰。
    """
    w = sbox(STAGE, scale)[2] - sbox(STAGE, scale)[0]
    fallback = [(k + 0.5) * w / N_SLOTS for k in range(N_SLOTS)]
    st = crop(frame, win, STAGE, scale)
    if st is None:
        return fallback
    h = st.shape[0]
    v = cv2.cvtColor(st, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
    prof = v[int(0.05 * h):int(0.25 * h)].mean(axis=0)
    rng = float(prof.max() - prof.min())
    if rng < 1e-6:
        return fallback
    prof = (prof - prof.min()) / rng
    try:
        from scipy.signal import find_peaks
    except ImportError:
        return fallback
    pk, _ = find_peaks(prof, height=0.35, distance=int(w / 8))
    if len(pk) != N_SLOTS:
        return fallback
    return [float(p) for p in sorted(pk)]


def slot_of(x, centers=None, width=None):
    """把舞台區內的 x 座標換成槽號 1..4。

    給了 centers 就用最近的槽心(對聚光燈校準結果穩健);否則退回等分。
    """
    if centers:
        return int(np.argmin([abs(x - c) for c in centers])) + 1
    w = width or (STAGE[2] - STAGE[0])
    return int(np.clip(x // (w / N_SLOTS), 0, N_SLOTS - 1)) + 1


# ---------------------------------------------------------------- 作答按鈕
def buttons_lit(frame, win, scale=1.0):
    """四顆作答按鈕是否亮起(變藍)= 現在是作答窗口。回 (bool, 藍色面積比)。

    這是比計分格【可靠得多】的切輪訊號:
      - 計分格只在答對時才填,失敗場整場空白,切不出輪次
      - 計分格會被遊戲的黃色準星特效蓋住而誤讀
      - 按鈕變藍每輪必然發生,不管答對答錯
    而且它同時就是「該點擊了」的觸發時機。
    實測全片抓到 16 個作答窗口,與 3 場完整比賽的 12 輪完全對得上。
    """
    vals = []
    for b in BUTTONS:
        c = crop(frame, win, b, scale)
        if c is None:
            return False, 0.0
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        vals.append(float((((H >= 85) & (H <= 110)) & (S > 90) & (V > 90)).mean()))
    m = float(np.mean(vals))
    return m > BUTTON_LIT_MIN, m


def button_center(win, slot, scale=1.0):
    """槽號 1..4 → 該按鈕中心的【完整幀】座標,給點擊用。"""
    x1, y1, x2, y2 = sbox(BUTTONS[int(slot) - 1], scale)
    return (win[0] + (x1 + x2) // 2, win[1] + (y1 + y2) // 2)


# ---------------------------------------------------------------- 計分格
def read_scoreboard(frame, win, scale=1.0):
    """讀四格計分格,回 [(顏色, 數字或 None), ...]。

    顏色: "empty" | "yellow"(答對) | "red"(整場失敗結算) | "other" | "oob"
    數字: "0".."4";0 出現在失敗歸零畫面(0000)。

    【注意】計分格會被遊戲的黃色十字準星特效蓋過去,那時會誤讀成有內容。
    呼叫端要用時間穩定性過濾(連續多幀一致才採信),不要單幀採信。
    """
    out = []
    tpls = _digit_tpl()
    for box in SCORE_CELLS:
        c = crop(frame, win, box, scale)
        if c is None:
            out.append(("oob", None))
            continue
        if scale != 1.0:
            # 數字模板是 scale=1 抽的,把 cell 還原回原尺寸再比,模板不必跟著縮放
            tw = SCORE_CELLS[0][2] - SCORE_CELLS[0][0]
            th = SCORE_CELLS[0][3] - SCORE_CELLS[0][1]
            c = cv2.resize(c, (tw, th), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        ink = (V > 110) & (S > 90)
        if ink.sum() < 8:
            out.append(("empty", None))
            continue
        h = H[ink]
        if ((h >= 18) & (h <= 38)).mean() > 0.5:
            col = "yellow"
        elif ((h <= 12) | (h >= 168)).mean() > 0.5:
            col = "red"
        else:
            col = "other"
        best, bi = None, -1.0
        for d, t in tpls.items():
            iou = (ink & t).sum() / max((ink | t).sum(), 1)
            if iou > bi:
                best, bi = d, float(iou)
        out.append((col, best if bi > 0.35 else None))
    return out


def score_filled(cells):
    """計分格已填幾格(黃或紅都算)。"""
    return sum(1 for c in cells if c[0] in ("yellow", "red"))


# ---------------------------------------------------------------- 展示階段
def is_reveal(frame, win, scale=1.0):
    """是不是展示階段(紅簾幕白背景,四隻清楚露臉)。回 (bool, 高亮占比)。

    展示階段與洗牌階段的畫面亮度差異極大:前者是白舞台,後者是暗背景 + 彩色
    聚光燈。用舞台區的高亮像素占比就分得開。
    """
    st = crop(frame, win, STAGE, scale)
    if st is None:
        return False, 0.0
    v = cv2.cvtColor(st, cv2.COLOR_BGR2HSV)[:, :, 2]
    f = float((v > 190).mean())
    return f >= 0.50, f


def find_arrow(frame, win, centers=None, scale=1.0):
    """展示階段:從橘色箭頭讀出目標的初始槽。回 (slot 或 None, 面積)。

    箭頭在【蘑菇頭頂上方】,所以只在舞台上部 40% 搜尋 —— 蘑菇身上的橘色蝴蝶結
    是主要干擾源,不設這個限制的話分布會被它污染(實測未限制時某場出現
    槽1:24/槽3:15 的假分裂,限制後變成乾淨的 槽1:24/槽4:1)。
    """
    st = crop(frame, win, STAGE, scale)
    if st is None:
        return None, 0
    hsv = cv2.cvtColor(st, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    m = ((H >= 3) & (H <= 18) & (S > 140) & (V > 150)).astype(np.uint8)
    m[int(m.shape[0] * 0.40):] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None, 0
    j = max(range(1, n), key=lambda k: stats[k, cv2.CC_STAT_AREA])
    area = int(stats[j, cv2.CC_STAT_AREA])
    if area < int(25 * scale * scale):        # 面積門檻隨縮放走平方
        return None, area
    w = sbox(STAGE, scale)[2] - sbox(STAGE, scale)[0]
    return slot_of(float(cent[j][0]), centers, w), area


# ---------------------------------------------------------------- 追蹤
def band_energy(bands):
    """一疊【蘑菇水平帶】影像(灰階或 BGR 彩色)→ 每幀的 x 方向前景能量剖面,shape (T, W)。

    彩色輸入會在這裡轉灰再處理,灰階輸入的行為與加上這段之前【逐位元相同】。
    採集端(fiona_collect)改存彩色是為了讓偵測器拿得到顏色線索(見那裡的註解),
    這條追蹤路徑本身不吃顏色。

    兩層處理,缺一不可:
      1. 高通(原圖 - 大核高斯):聚光燈是平滑漸層而且顏色全程在變,高通後幾乎
         消失;蘑菇是有邊緣的結構,高通後輪廓清楚浮現。
      2. 時間中位數背景減除:舞台的靜態紋理比半透明蘑菇更強,不減掉的話模板
         匹配會【黏在背景上】(實測過:相關係數高達 0.86 卻整段沒動,追的是背景)。

    【非因果】median 需要整段影像。實機用法是在作答倒數觸發時,對「本輪開始
    到現在」這段一次算完 —— 那時整輪洗牌已經發生完,不需要逐幀即時輸出。
    """
    if len(bands) < 5:
        return None
    hp = []
    for g in bands:
        if g.ndim == 3:                 # 彩色帶(fiona_collect 現在存彩色)→ 這裡轉灰
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        g = g.astype(np.float32)
        hp.append(g - cv2.GaussianBlur(g, (0, 0), 9))
    hp = np.array(hp)
    fg = np.abs(hp - np.median(hp, axis=0))
    P = fg.mean(axis=1)
    P = P - P.min(axis=1, keepdims=True)
    return P / (P.max(axis=1, keepdims=True) + 1e-6)


def viterbi_track(P, x0, max_step=MAX_STEP_PX, lam=SMOOTH_LAMBDA, start_tol=12):
    """在能量圖 P (T, W) 上求從 x0 出發的全域最優路徑,回長度 T 的 x 陣列。

    成本 = -能量,轉移限制 |dx| <= max_step,另加 lam*|dx| 的平滑懲罰讓路徑在
    證據薄弱處保持慣性(唯一一次實測失敗就是目標連續 5 幀失去證據後被鄰近軌跡
    吸走)。start_tol 容許起點有一點誤差。
    """
    T, W = P.shape
    cost = -P
    INF = 1e9
    dp = np.full((T, W), INF)
    bk = np.zeros((T, W), np.int32)
    lo, hi = max(0, int(x0) - start_tol), min(W, int(x0) + start_tol + 1)
    dp[0, lo:hi] = cost[0, lo:hi]
    for t in range(1, T):
        for dx in range(-max_step, max_step + 1):
            sh = np.full(W, INF)
            if dx > 0:
                sh[dx:] = dp[t - 1, :W - dx]
            elif dx < 0:
                sh[:dx] = dp[t - 1, -dx:]
            else:
                sh = dp[t - 1].copy()
            # 平滑懲罰【必須在比較之前】加進去。曾經寫成選完最小值之後才加
            # (dp[t] += cost + lam*|bk|),那樣選轉移時根本沒考慮懲罰,λ 只是
            # 事後記帳,行為會很怪 —— 這正是 λ 一度極度敏感(0.10 全對、0.20
            # 崩掉)的原因。合成的斜線軌跡測試把它抓出來了。
            sh = sh + lam * abs(dx)
            b = sh < dp[t]
            dp[t][b] = sh[b]
            bk[t][b] = dx
        dp[t] += cost[t]
    e = int(np.argmin(dp[-1]))
    path = [e]
    for t in range(T - 1, 0, -1):
        e = int(np.clip(e - bk[t][e], 0, W - 1))
        path.append(e)
    return np.array(path[::-1])


def path_confidence(P, path):
    """路徑上的能量統計。回 dict:median / min / weak_frac(能量 <0.3 的幀比例)。

    【目前還不能拿來當信心指標】。原本預期 weak_frac 高 = 追蹤沒有證據支撐 =
    容易出錯,但 12 輪實測【不支持】這個假設:唯一失敗的那輪 weak_frac 只有
    0.04,而答對的輪次反而出現 0.16 / 0.21 / 0.29。兩者分不開。

    保留這個函式是為了讓採集器把它記下來,等實機資料累積夠了再回頭看有沒有
    別的統計量(例如次佳路徑的成本差、路徑在槽邊界附近徘徊的時間)能真正預測
    失敗。在那之前,不要用它做「要不要下注」的決策。
    """
    e = np.array([P[t, int(path[t])] for t in range(len(path))])
    return {"median": float(np.median(e)), "min": float(e.min()),
            "weak_frac": float((e < 0.3).mean())}
