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
#
# 【這道驗證是整條 CV 的天花板,而且動不得 —— 三種替代方案全部試過】
# 用 box 真值量過:即使【定位完美】,這道驗證也會擋下 39% 判向本來正確的樣本
# (全對 90/179 = 50%,被擋 71 張,方向真的判錯只有 17 張)。所以它就是上限所在。
# 但三條放寬/替換的路都更糟 —— 關鍵在【兩種失敗的代價不對等】:
#   誤按   = 真的按下錯方向鍵,白燒一次符文冷卻,還要等 RETRY_WAIT 才能重試
#   不輸出 = 退回 2 線 claude 判讀(約 4.8s),符文照樣解得開
#
#   ① 放寬面積門檻(182 張實測)
#        (250,650) 2.0  全對 40%  誤按  9  ← 維持
#        (120,1100) 4.0 全對 43%  誤按 23
#        (80,1500)  6.0 全對 46%  誤按 32,負樣本失守(2/2 → 1/2)
#   ② 改用箭頭模板吻合度:單獨用時放行正例 72% 但也放行 20% 的負例(現行只放行 1%)
#   ③ 改用膠囊邊緣模板吻合度:理想情境下略優(正例 55% vs 52%),端到端卻是
#        全對 46% 但誤按 42 張(現行 9 張)、負樣本失守。
#      原因是【循環論證】—— 定位本來就是拿同一個模板找分數最高的位置,再用那個
#      分數驗證,定位錯的地方一樣高分。面積驗證的價值正在於它【獨立】:走色度分割,
#      與定位方法無關,所以擋得住定位錯誤。
#
#   ④ 其他找過的替代特徵(正例/負例以真值 box 與偏移 120~220px 的 box 量):
#        面積中位+至少3格合格   正例 77% 負例 12%   ← 對單格遮擋穩健,但擋不住雜訊
#        四格箭頭中心 y 對齊    正例 72% 負例 27%(門檻 18px)
#        y 對齊 + 面積中位      正例 67% 負例  7%
#        y 對齊 + 間距一致      正例 16% 負例  0%   ← 太嚴,間距本身就不穩
#        膠囊內部色度標準差     幾乎沒有分辨力(正例中位 29.4 / 負例 18.4,大量重疊,
#                            而且方向與直覺相反 —— 膠囊內有四支彩色箭頭反而更雜)
#        內外色度距離          正例負例中位都是 2.2,完全無分辨力
#      沒有任何一種能同時做到「正例 >55% 且負例 ~0%」。現行規則之所以難以超越,
#      是因為「四格面積都落在窄範圍且彼此接近」本身就是很強的幾何+尺度聯合約束,
#      隨機位置幾乎不可能滿足。
#
# 結論:這個門檻要往「寧可不報」偏,不是往命中率偏 —— 被擋下的樣本會退回 2 線 claude,
# 符文照樣解得開,只是慢 4.8 秒;誤按則要白燒一次符文冷卻。代價不對等。
# 剩下那 45% 是箭頭真的被特效遮擋到面積失衡,那是資料本身的困難,不是門檻設錯。
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


def _find_by_edges(bgr):
    """靠【金色描邊】定位。回膠囊框 (x0, y0, x1, y1),找不到回 None。

    搜尋範圍由 search_band(縱向) + search_x(橫向) 界定 —— 掃整張畫面只會多引入
    誤判來源(實測:草地植被的假金線、小地圖邊框)。

    寬度一律鎖成 CAP_W:實測會量到 352~363(描邊與相鄰的黃色箭頭像素連通),不修正的話
    第 1 格會越過左端蓋、吃進膠囊外的背景,分割就會壞掉。

    【這條路的先天限制】描邊只有 1~3px 寬,在實戰幀裡會碎裂:取一張失敗案例逐像素查,
    搜尋範圍內有 7027 個金色像素,卻沒有任何寬度 >=150px 的連通元件。半透明 UI 疊在
    動態亮背景上,混色後大量像素落出金色範圍。所以它準的時候很準(誤差中位 2px),
    但實戰幀有六成完全找不到 —— 那部分交給 _find_by_template。"""
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


# ---------- 膠囊定位:邊緣形狀模板 ----------
# 膠囊的【整體輪廓】(上下緣 + 兩端圓角 + 四支箭頭的位置)由 127 張已知真值的樣本
# 取 Canny 邊緣後平均而成。用形狀比對而不是找連續金線,所以描邊碎裂也不影響 ——
# 這正是描邊法在實戰幀上失效的地方。
#
# 【度量必須是 CCOEFF 不能用 CCORR】實測差距極大:同一組模板,CCORR 只有 7% 落在
# 20px 內,CCOEFF 有 55%。CCOEFF 會先減去均值,對整體亮度變化免疫;CCORR 則會被
# 「邊緣多的區域」整片拉高分數,在特效滿天的畫面裡到處都是假匹配。
# 模板的建法也掃過(2-fold 交叉驗證,指標=定位誤差):直接平均最好。
#     平均 / 平均+置中 / 平均正規化   誤差中位 14,<=20px 58%(三者相同 —— CCOEFF
#                                   本身就會置中正規化,先做等於白做)
#     出現率>0.2 二值化              誤差中位 31
#     出現率>0.3 二值化              誤差中位 162
#     逐像素中位數                   誤差中位 322(邊緣稀疏,中位數把大部分像素抹成 0)
_CAP_TPL_PATH = paths.srv_res("rune_capsule_tpl.png")   # 唯讀資源:內嵌在 exe
_CAP_TPL = None
if os.path.exists(_CAP_TPL_PATH):
    _ct = cv2.imread(_CAP_TPL_PATH, cv2.IMREAD_GRAYSCALE)
    if _ct is not None and _ct.shape == (CAP_H, CAP_W):
        _CAP_TPL = _ct.astype(np.float32) / 255.0

TPL_MIN_SCORE = 0.15         # 低於此分數視為沒找到。實測 0.15 與 0(完全不設限)
                             # 表現相同,但留著它才擋得住「畫面上根本沒謎題」的情況;
                             # 0.25 以上會開始丟掉真的膠囊(40% → 28%)。


# 【試過 auto-maple 的兩個前處理技巧,都不採用 —— 但過程本身值得記著】
# 參考 github.com/tanjeffreyz/auto-maple 的 detection.py。它的路線與這裡不同:
# 用 TensorFlow 物件偵測直接找四支箭頭、再由箭頭的 bbox 反推膠囊,不定位膠囊本身。
# 其中兩個前處理可以脫離模型單獨借用,都實測過:
#
#   ① Canny(200,300)(他們的門檻):在我們的畫面上誤差中位從 14 惡化到 105。
#      門檻是跟著畫面調的,照抄沒有意義。
#
#   ② filter_color:先用絕對 HSV (1,100,100)~(75,255,255) 只留紅→綠(箭頭色)、
#      其餘清成黑,再抓邊緣。這個【在簡單樣本上確實有效】,只看真值可信的那 127 張時
#      定位 <=20px 從 65% 升到 69%、全對 49% → 51%。
#      但把評估集換成全部 182 張(含真值不可信的困難樣本)後就反過來:
#          無色彩過濾  全對 39%  單支 42%  誤按 8
#          有色彩過濾  全對 36%  單支 40%  誤按 11
#      原因是那個色域只涵蓋 H 1~75,而困難樣本正是箭頭被技能特效覆蓋成藍/紫的那些 ——
#      一濾就整支不見。簡單樣本受益、困難樣本受害,而我們要救的正是困難那批。
#
# 教訓:評估集選得太乾淨會得到相反的結論。要用含困難樣本的全集判斷。
def _canny_f32(bgr):
    return cv2.Canny(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 50, 150).astype(np.float32) / 255.0


def _find_by_template(bgr):
    """靠【邊緣形狀】定位。模板不在就回 None,由呼叫端只用描邊法。"""
    if _CAP_TPL is None:
        return None
    by0, by1 = search_band(bgr.shape[0])
    bx0, bx1 = search_x(bgr.shape[1])
    sub = bgr[by0:by1, bx0:bx1]
    if sub.shape[0] < CAP_H or sub.shape[1] < CAP_W:
        return None
    res = cv2.matchTemplate(_canny_f32(sub), _CAP_TPL, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, ml = cv2.minMaxLoc(res)
    if mx < TPL_MIN_SCORE:
        return None
    x, y = bx0 + ml[0], by0 + ml[1]
    return (x, y, min(bgr.shape[1], x + CAP_W), min(bgr.shape[0], y + CAP_H))


def _slot_consistency(bgr, box):
    """四格面積一致性。兩種定位法各給一個答案時用它當裁判 ——
    框對了四格各含一支箭頭、面積相近;框歪了會切到背景,面積立刻失衡。
    純粹比較用,不是「是不是膠囊」的驗證(那是 read_dirs 裡的 AREA_OK)。"""
    if box is None:
        return -1e9
    cap = bgr[box[1]:box[3], box[0]:box[2]]
    if cap.size == 0:
        return -1e9
    dist = _chroma_map(cap)
    w = cap.shape[1] / 4
    areas = []
    for k in range(4):
        m = _seg(dist, int(k * w), int((k + 1) * w))
        areas.append(0 if m is None else int((m > 0).sum()))
    if min(areas) <= 0:
        return -1e9
    return -(max(areas) / min(areas) - 1) * 100 - abs(sum(areas) / 4 - 440) * 0.05


def find_capsule(bgr):
    """回膠囊框 (x0, y0, x1, y1),找不到回 None。

    兩種定位法【都跑】,再用四格面積一致性挑一個 —— 這是 2-fold 交叉驗證出來的最佳
    組合(每張都在沒參與建模板時被評估):
        現行描邊法          14%
        純模板             42%
        模板優先+描邊退路      43%
        兩者取面積較一致者     44%   ← 採用
    差別在於兩者失敗的樣本不重疊:描邊法在描邊完整時精準(誤差中位 2px)但實戰幀常
    整個找不到;模板法找得到但誤差中位 16px。讓面積一致性當裁判,兩邊的長處都留住。
    負樣本(畫面上沒謎題)在四種策略下都是零誤判。

    【定位精度是斷崖式的,20px 是懸崖】用 box 真值分層量過端到端判對率:
        誤差 <=10px  79%
        誤差 11~20px 58%
        誤差 21~40px  0%     ← 掉到零
        誤差 >40px    3%
    箭頭寬 28、格寬 82,偏超過 20px 就開始把隔壁格的箭頭切進來。所以定位改進的
    價值全在「把誤差壓進 20px」,壓不進去的話再怎麼接近都沒有用。

    【局部精修試過,無效】從模板定位的結果出發,在 ±20px 內用評分函數微調:
        精修前          誤差中位 13   <=20px 60%
        用四格面積一致性   誤差中位 31   <=20px 20%
        用箭頭模板吻合度   誤差中位 34   <=20px 15%
    三種評分都把框推離真值 —— 它們在真值位置並不是最大值,拿來當導引只會越修越糟。"""
    edge = _find_by_edges(bgr)
    tpl = _find_by_template(bgr)
    if edge is None:
        return tpl
    if tpl is None:
        return edge
    return edge if _slot_consistency(bgr, edge) >= _slot_consistency(bgr, tpl) else tpl


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

    【CNN 嘗試過,沒過驗收,預設關閉】曾試著用 CNN 取代分割那一段:膠囊定位零誤差、
    模板判向在乾淨輸入上 97.5%,但整體只有 41% —— 差距全在 _chroma_map/_seg 被怪物
    與技能特效污染。CNN 實測整體正確率反而更低(見 rune_nn.py 開頭表格),驗收沒過,
    所以現在(預設)整條照舊走 _read_dirs_chroma(色度分割)。要做實驗才手動開
    MAPLE_RUNE_NN=1,開啟後也只是嘗試,不是既定方向。

    strict=True 會做「這真的是膠囊嗎」的驗證。**不要為了提高偵測率把它關掉**:
    誤判的代價不是漏一次,而是拿背景雜訊當箭頭去按方向鍵,白燒一次符文冷卻。
    預覽用 strict=False 才看得到「抓到什麼」以便診斷。
    """
    import rune_nn
    box = find_capsule(frame_bgr)
    if box is None:
        return [], "找不到謎題膠囊(還沒開謎題?)"
    if not rune_nn.available():
        return _read_dirs_chroma(frame_bgr, box, strict)

    crops = []
    for x0, y0, x1, y1 in slots(box, 4):
        c = frame_bgr[y0:y1, x0:x1]
        if c.size == 0:
            return [], "膠囊區域為空"
        crops.append(c)
    dirs, probs = rune_nn.predict(crops)
    if len(dirs) != 4:
        return _read_dirs_chroma(frame_bgr, box, strict)
    if not strict:
        return [d if d != "none" else None for d in dirs], ""
    # 守門取代舊的四格面積閘門:任一格是 none、或最低信心度不足,就整組退線。
    if "none" in dirs:
        return [], f"不像謎題膠囊(第 {dirs.index('none') + 1} 格判為非箭頭)"
    lo = min(probs)
    if lo < rune_nn.MIN_PROB:
        return [], (f"信心不足(最低 {lo:.2f} < {rune_nn.MIN_PROB:.2f}),"
                    f"暫定 {dirs}")
    return dirs, ""


def _read_dirs_chroma(frame_bgr, box, strict):
    """舊路徑:色度分割 + 模板判向。模型不在時的完整退路。

    【不要刪掉】沒帶模型的打包版、以及模型載入失敗時,整條流程都靠它。
    """
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
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _put_fit(img, text, org, color, max_w, base=0.45):
    """把文字塞進 max_w 寬:量得太寬就逐級縮字。預覽的 scale 可以是 1~6,
    固定字級在 scale=1 時會整段溢出畫面外,那條資訊等於沒有。"""
    fs = base
    while fs > 0.28:
        (tw, _), _ = cv2.getTextSize(text, _FONT, fs, 1)
        if tw <= max_w - 8:
            break
        fs -= 0.04
    cv2.putText(img, text, org, _FONT, fs, color, 1, cv2.LINE_AA)


def preview(frame_bgr, scale=3):
    """回一張標註後的膠囊預覽圖(BGR),供中控頁確認定位與判讀是否正確。

    圖上一律標出【原始影格座標】(頂部兩行:capsule 實際切在哪、search 允許切在哪)。
    只給放大後的裁切圖是不夠的 —— 畫面上看起來對的膠囊,可能是在錯的位置抓到的
    同色雜訊,不把座標寫出來就無從二次確認。

    找不到膠囊時回整幀縮圖 + 搜尋範圍框 —— 回 None 的話前端只會看到破圖,
    分不出「沒開謎題」與「定位壞了」;畫出搜尋框才看得出是「該找的地方沒找到」
    還是「膠囊根本落在搜尋範圍外」。"""
    fh, fw = frame_bgr.shape[:2]
    by0, by1 = search_band(fh)
    bx0, bx1 = search_x(fw)
    search_txt = f"search X {bx0}-{bx1}  Y {by0}-{by1}   frame {fw}x{fh}"

    box = find_capsule(frame_bgr)
    if box is None:
        k = 480 / max(1, fw)
        small = cv2.resize(frame_bgr, (int(fw * k), int(fh * k)))
        cv2.rectangle(small, (int(bx0 * k), int(by0 * k)),
                      (int(bx1 * k), int(by1 * k)), (0, 200, 255), 1)
        TOP = 20
        vis = np.zeros((TOP + small.shape[0], small.shape[1], 3), np.uint8)
        vis[TOP:] = small
        _put_fit(vis, search_txt, (6, 14), (0, 200, 255), vis.shape[1])
        cv2.putText(vis, "no capsule", (8, TOP + 24), _FONT, 0.7, (60, 60, 240), 2)
        return vis

    pad = 6
    x0 = max(0, box[0] - pad)
    y0 = max(0, box[1] - pad)
    cap = frame_bgr[y0:min(fh, box[3] + pad), x0:min(fw, box[2] + pad)]
    img = cv2.resize(cap, (cap.shape[1] * scale, cap.shape[0] * scale),
                     interpolation=cv2.INTER_NEAREST)
    # 座標帶在上、方向帶在下,都不疊在影像上 —— 疊上去會被膠囊邊緣切掉,而且蓋住箭頭
    TOP, BAND = 36, 34
    vis = np.zeros((TOP + img.shape[0] + BAND, img.shape[1], 3), np.uint8)
    vis[TOP:TOP + img.shape[0]] = img
    # 第 1 行是實際切出來的框,第 2 行是允許的範圍。兩行並排才看得出膠囊有沒有
    # 頂到搜尋邊界(貼邊 = 再偏一點就會整個抓不到,是收窄過頭的前兆)。
    cw, ch = box[2] - box[0] + 1, box[3] - box[1] + 1
    _put_fit(vis, f"capsule X {box[0]}-{box[2]}  Y {box[1]}-{box[3]}   {cw}x{ch}",
             (6, 14), (80, 240, 120), vis.shape[1])
    _put_fit(vis, search_txt, (6, 30), (0, 200, 255), vis.shape[1])
    # 預覽同時顯示嚴格判定的結果與寬鬆讀到的內容:被驗證擋下時仍畫出方向,
    # 才看得出來「擋掉的是誤判還是誤殺」。
    strict_dirs, verdict = read_dirs(frame_bgr, strict=True)
    dirs, _ = read_dirs(frame_bgr, strict=False)
    if verdict:
        cv2.putText(vis, verdict[:46], (6, TOP + 18), _FONT, 0.5, (60, 60, 240), 2)
    cv2.rectangle(vis, (pad * scale, TOP + (box[1] - y0) * scale),
                  ((box[2] - x0) * scale, TOP + (box[3] - y0) * scale), (0, 200, 255), 2)
    sw = (box[2] - box[0]) / 4
    for k in range(4):
        sx = int((box[0] - x0 + k * sw) * scale)
        ex = int((box[0] - x0 + (k + 1) * sw) * scale)
        if k:
            cv2.line(vis, (sx, TOP + (box[1] - y0) * scale),
                     (sx, TOP + (box[3] - y0) * scale), (0, 200, 255), 1)
        d = dirs[k] if k < len(dirs) else None
        # 綠=採用;黃=讀到但被驗證擋下(疑似誤判);紅=讀不出來
        col = (80, 240, 120) if strict_dirs else ((60, 200, 240) if d else (60, 60, 240))
        cv2.putText(vis, _ARROW_GLYPH.get(d, "?"),
                    (sx + (ex - sx) // 2 - 9, TOP + img.shape[0] + BAND - 8),
                    _FONT, 0.9, col, 2)
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
