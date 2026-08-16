"""從 RT-DETR 箭頭偵測器的原始輸出裡,挑出「最合理的 4 支箭頭」組合。

背景見 .superpowers/detr-select.md:aspect 模型(1344x384,等比輸入)偵測品質
(recall、定位誤差)明顯優於 squash 模型(640x640,箭頭被壓扁),但因為每張圖多
出來的高分假陽性框變多(平均 3.02 -> 4.60 個 >=0.05 分的框),現行「直接取分數
最高 4 個」的規則反而把真箭頭擠掉,端到端四支全對率從 67.0% 掉到 53.8%。

這個模組只做「從既有偵測框集合中挑選」,不補全、不外插、不發明新框:
    - 候選不足 4 個 -> 回傳 None(失敗,交給上層退回 2 線)
    - 對所有 C(n,4) 組合窮舉評分(候選數先用貪婪 NMS 裁到
      max_candidates_before_prefilter 個以內,窮舉完全可行)
    - 評分同時看:偵測分數、同一列(y 中心相近)、x 間距是否接近整排常見間距
      (軟性偏好,不是硬約束 —— 已實測箭頭不是嚴格等距置中,格中心誤差標準差
      13.7px)、框大小是否彼此接近、4 框互不重疊(IoU 太高視為同一支箭頭的
      重複偵測,整個組合直接淘汰)

純函式,不依賴 torch / GPU,不讀寫任何檔案,方便被評估腳本大量呼叫。內部刻意
不用 numpy 處理單一候選組合(4 個元素)的統計量 —— 這種規模下 numpy 的呼叫
開銷比計算本身還貴,窮舉 C(n,4) 次的場合改用純 Python 算術明顯快上一個數量級
(調參時實測:aspect 模型、min_score=0.015、246 張圖,一組參數原本要 33.8 秒,
改寫後 <2 秒)。
"""
import itertools

# 預設參數:見 .superpowers/detr-select.md「權重與容忍度」一節的說明與理由。
DEFAULT_PARAMS = dict(
    min_score=0.05,       # 候選框最低分數門檻(與 eval_rune_detr.py 的 --min-score 一致)
    overlap_iou_reject=0.3,  # 組合內任兩框 IoU 超過此值 -> 視為同一支箭頭的重複偵測,整組淘汰
    w_score=6.0,           # 偵測分數(均值)的權重
    sigma_y=6.0,            # y 中心標準差的容忍尺度(px)
    sigma_gap=20.0,          # x 間距與期望間距落差的容忍尺度(px,軟性)
    gap_expected=89.0,       # 期望的相鄰箭頭 x 中心間距(px,由 246 張訓練集標註實測得出)
    sigma_size=12.0,         # 框寬高彼此差異的容忍尺度(px)
    max_candidates_before_prefilter=12,  # 候選框數超過此值時,先用貪婪 NMS 裁到這個數量再窮舉
    prefilter_nms_iou=0.6,
)


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = x1 - x0, y1 - y0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _greedy_nms_trim(boxes, scores, keep_n, iou_thresh):
    """分數高到低貪婪保留,IoU 超過門檻的較低分框先丟,直到 <= keep_n 個或沒得丟。
    只是為了把候選數壓到窮舉可行的範圍,不是最終的重疊判定(那個在組合評分裡做)。"""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    kept = []
    for i in order:
        if any(_iou(boxes[i], boxes[j]) > iou_thresh for j in kept):
            continue
        kept.append(i)
    if len(kept) >= keep_n:
        return kept[:keep_n]
    kept_set = set(kept)
    for i in order:
        if len(kept) >= keep_n:
            break
        if i not in kept_set:
            kept.append(i)
            kept_set.add(i)
    return kept


def _combo_score(bs, ss, p):
    """bs/ss: 長度剛好 4 的 list(box tuple / score)。回傳 (goodness, 依 x 排序後的 bs)
    或 None(組合內有重疊,直接淘汰)。全部用純 Python 算術,不用 numpy
    (見檔案開頭說明:對這麼小的陣列,numpy 呼叫開銷比計算本身貴)。"""
    for i in range(4):
        bi = bs[i]
        for j in range(i + 1, 4):
            if _iou(bi, bs[j]) > p["overlap_iou_reject"]:
                return None

    order = sorted(range(4), key=lambda k: bs[k][0] + bs[k][2])
    bs = [bs[k] for k in order]
    ss = [ss[k] for k in order]

    yc = [(b[1] + b[3]) * 0.5 for b in bs]
    xc = [(b[0] + b[2]) * 0.5 for b in bs]
    ws = [b[2] - b[0] for b in bs]
    hs = [b[3] - b[1] for b in bs]

    y_mean = sum(yc) / 4.0
    y_std = (sum((v - y_mean) ** 2 for v in yc) / 4.0) ** 0.5

    gap_expected = p["gap_expected"]
    gap_dev = (abs(xc[1] - xc[0] - gap_expected)
               + abs(xc[2] - xc[1] - gap_expected)
               + abs(xc[3] - xc[2] - gap_expected)) / 3.0

    w_mean = sum(ws) / 4.0
    w_std = (sum((v - w_mean) ** 2 for v in ws) / 4.0) ** 0.5
    h_mean = sum(hs) / 4.0
    h_std = (sum((v - h_mean) ** 2 for v in hs) / 4.0) ** 0.5
    size_dev = w_std + h_std

    mean_score = sum(ss) / 4.0

    goodness = (
        p["w_score"] * mean_score
        - (y_std / p["sigma_y"]) ** 2
        - (gap_dev / p["sigma_gap"]) ** 2
        - (size_dev / p["sigma_size"]) ** 2
    )
    return goodness, bs


def select_arrows(boxes, scores, params=None):
    """boxes: (N,4) xyxy(list/array 皆可), scores: (N,)。

    回傳 4 個 (x0,y0,x1,y1) tuple 組成的 list(依 x 中心由左到右排序),
    或 None(候選不足 4 個,或所有組合都因重疊被淘汰 -> 選不出來,回報失敗)。
    """
    p = DEFAULT_PARAMS if params is None else {**DEFAULT_PARAMS, **params}
    min_score = p["min_score"]
    boxes_l = [tuple(b) for b, s in zip(boxes, scores) if s >= min_score]
    scores_l = [float(s) for s in scores if s >= min_score]
    n = len(boxes_l)
    if n < 4:
        return None

    if n > p["max_candidates_before_prefilter"]:
        idx = _greedy_nms_trim(boxes_l, scores_l, p["max_candidates_before_prefilter"],
                                p["prefilter_nms_iou"])
        boxes_l = [boxes_l[i] for i in idx]
        scores_l = [scores_l[i] for i in idx]
        n = len(boxes_l)
        if n < 4:
            return None

    best = None
    for idxs in itertools.combinations(range(n), 4):
        bs4 = [boxes_l[i] for i in idxs]
        ss4 = [scores_l[i] for i in idxs]
        res = _combo_score(bs4, ss4, p)
        if res is None:
            continue
        sc, bs_sorted = res
        if best is None or sc > best[0]:
            best = (sc, bs_sorted)
    if best is None:
        return None
    return best[1]


def top4_select(boxes, scores, params=None):
    """對照組:現行線上規則,單純取分數最高的 4 個,不套用任何分數門檻
    (與 tools/eval_rune_detr.py 的 end-to-end 區塊、以及正式服務 rune_cv 的行為
    一致:RT-DETR 一次回傳 300 個 query,不管分數多低,直接取分數最高的 4 個,
    候選「不足 4 個」這件事在這個規則下幾乎不會發生)。"""
    n = len(boxes)
    if n < 4:
        return None
    order = sorted(range(n), key=lambda i: -scores[i])[:4]
    det4 = [tuple(boxes[i]) for i in order]
    det4.sort(key=lambda b: b[0] + b[2])
    return det4
