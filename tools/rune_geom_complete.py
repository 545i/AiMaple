"""幾何補全:利用「畫面上就是 4 支箭頭、大致排成一列、間距相近」這個先驗,把
RT-DETR 漏掉的箭頭位置補回來。純程式邏輯,不碰模型、不重訓。

背景:RT-DETR(見 tools/train_rune_detr.py / tools/eval_rune_detr.py)recall 只有
0.805,兩成箭頭沒被偵測到,只有 73.5% 的圖剛好偵測到 4 支。但抓到的都很準
(中位定位誤差 0.74px),問題純粹是「有沒有抓到」不是「準不準」。

【統計依據】用 rune_dataset/detr_annotations_full.json(364 張圖,見
tools/rune_detr_dataset_build.py --min-blob 0 產生)裡剛好有全部 4 支箭頭真值框的
360 張圖算出來的經驗值(見本檔案 __main__ 區塊可重算):
    - 相鄰箭頭中心 x 間距:中位數 87.0px,std 16.3px(min 29 / max 132.5,
      p10 65.5 / p90 109)—— 間距不是常數,只能當「軟性偏好」,不能當硬約束。
      這與已知的「格中心 x 偏差 std 13.7px」是同一件事的另一種量法(箭頭不是
      切格置中的)。
    - 同一列箭頭中心 y 的最大跨距(y-span):中位數 13.25px,std 11.9px,
      p90 37.1px —— 用來設定「同一列」判定的容忍度。
    - 箭頭框寬:中位數 27.5px;框高:中位數 27.0px —— 補位置時用來合成缺格的
      估計框大小。

【演算法】所有分支(2 支/3 支/4+支)統一用同一套「槽位指派 + 局部線性配適」:
    已知的 K 個候選中心 x(由左到右排序)要對應到 4 個槽位(0..3,由左到右)裡的
    哪 K 個 —— 槽位順序必須遞增(因為 x 由左到右排序,槽位本來就是左到右)。
    對每一種可能的遞增槽位指派,算出「局部間距」gap_local =
    (x_last - x_first) / (slot_last - slot_first),用它反推所有已知點之間應該有的
    間距,跟實際間距的殘差平方和 + 對「經驗中位間距」的正則化項,取代價最低的
    指派。K=2 時殘差恆為 0(只有一段間距),完全由正則化項決定(實際間距接近
    87px 就傾向判成相鄰槽位,接近 174px 就傾向判成隔一格,以此類推)。K=3 時
    殘差項會自動判斷缺的是頭尾(外插)還是中間(內插),不用另外寫 if/else。

    偵測到 >=4 支的情況不是槽位指派問題,是「這堆候選裡哪 4 個才是真箭頭」的
    選擇問題:候選數 > 4 時,對分數最高的一小撮候選(預設 top 8,C(8,4)=70 組
    合,可接受的暴力枚舉)逐一算「等間距殘差 + 間距正則化 - 分數獎勵」,取
    代價最低的 4 個。分數獎勵的係數(score_beta)決定「為了間距更整齊,願意
    放棄多少分數」,見 SCORE_BETA 常數旁的說明。

【弱回收,實測必要】第一版只用純幾何合成框補缺格,結果在驗證集上比「完全不管
    門檻、永遠取分數最高 4 個」的既有基準(round1 手法)還差(3->1 分支端到端
    只有 7.7%,遠低於基準的 74.5%)。原因是 det_threshold 只是「算幾支」用的
    保守門檻,不代表低於門檻的 query 完全沒訊號 —— recall 0.805 本來就代表
    有一批箭頭真的被偵測到、只是分數沒過門檻(信心校準差,不是定位壞了)。
    所以缺格不會馬上用合成框,而是先在「分數 >= WEAK_FLOOR(遠低於主門檻)」
    的全部候選裡,依幾何推算出的預期位置 ±半個 gap_local 視窗找真正的偵測框
    (_recover_weak),找到才退回合成框。這個修正後 3->1 分支的端到端才總算
    追上(見 tools/eval_rune_geom.py 的實測輸出,報告會附完整數字)。

【已知限制,如實記錄】
    - 補出來的位置沒有偵測器自己的方向類別(缺格本來就沒有偵測框可言),但
      這不影響最終判向 —— 下游 rune_cv._chroma_map/_seg/_direction_tpl 本來就是
      拿框圍出的區域重新跑色度分割,不依賴偵測器自己的分類頭。
    - 2 支推 2 支的組合空間比 3 支推 1 支大很多(C(4,2)=6 vs C(4,3)=4 且 K=2 沒有
      殘差項可用,純靠間距先驗),先驗証錯的風險本來就比較高,所以任務要求
      分開統計成功率,不能跟 3 支的混報。
    - 全部用「軟性」代價函數,沒有寫死任何硬性間距上下限 —— 前提是任務規格
      明講「箭頭不是嚴格等距置中」,寫死範圍等於是自己加了一個沒被驗證過的
      假設。
"""
import itertools
import os

import numpy as np

# ---------- 經驗先驗(算法見檔案開頭說明;可用 __main__ 重新算一次驗證) ----------
GAP_TYP = 87.0          # 相鄰箭頭中心 x 間距,經驗中位數(px)
GAP_REG_WEIGHT = 0.02   # 間距正則化權重:1px 偏離 gap_typ 相當於 0.02 px^2 代價
                        # (等價於容忍度 ~ sqrt(1/reg_weight) ~= 7px 尺度上代價才開
                        # 始跟殘差項同量級;刻意設得寬鬆,因為間距 std 本身就有
                        # 16px,不該讓正則化蓋過真正的殘差證據)
ROW_Y_TOL = 30.0        # 「同一列」判定容忍度(px)。經驗 y-span p90 是 37.1px,
                        # 取比 p90 略緊的 30px:寧可漏判(→退化成偵測數更少、
                        # 觸發別的補全分支甚至放棄),不要把明顯偏離列位置的
                        # 背景框也當作候選(那樣才是真正污染下游判向)。
DEFAULT_DET_THRESHOLD = 0.05   # 沿用 eval_rune_detr.py 的理由:RT-DETR 一對一
                                # 匹配、不需要 NMS,分數斷層在 ~0.05,不是 0.5。
MAX_POOL_FOR_SELECT = 8        # >=4 支候選時,只在分數最高的前 N 個裡做組合枚舉
                                # (C(8,4)=70,夠用又便宜)。
SCORE_BETA = 300.0      # >=4 支選 4 支時,分數換算成等價像素代價的係數:放棄
                        # 0.01 分數,換取殘差(間距不整齊程度)降低 3px 才划算。
                        # 取這個量級是因為 RT-DETR 信心值本身校準得很差
                        # (top-1 通常只有 0.1~0.2,見 detr-detect.md),分數差距
                        # 天生就小,係數太大會讓分數幾乎不起作用、太小會讓背景
                        # 框只要間距湊巧整齊就贏過真箭頭 —— 300 是兩者間的折衷,
                        # 已用驗證集(blob_n>=10, 51 張)檢查過不會讓 exact-4 圖的
                        # 選擇結果變差(見 tools/eval_rune_geom.py 的輸出)。

WEAK_FLOOR = 0.01        # 「弱回收」搜尋用的最低分數地板。RT-DETR 300 個 query
                        # 裡背景 query 分數通常貼著 0,真正對到物件的 query 就算
                        # 沒過主門檻也還是會比純背景明顯——這裡刻意設得比
                        # DEFAULT_DET_THRESHOLD 低很多,目的是把「recall 不到主
                        # 門檻但其實有偵測到」的箭頭找回來,見 _recover_weak() 說明。
WEAK_SEARCH_FRAC = 0.5   # 弱回收的搜尋視窗半寬 = WEAK_SEARCH_FRAC * gap_local。
                        # 0.5 代表視窗剛好覆蓋到兩個槽位中間,不會誤搶到鄰居槽位
                        # 的候選。

BRANCH_4 = "4"
BRANCH_3TO1 = "3->1"
BRANCH_2TO2 = "2->2"
BRANCH_FAIL = "fail_leq1"


def _box_wh(box):
    x0, y0, x1, y1 = box
    return x1 - x0, y1 - y0


def _center(box):
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _row_filter(cands, row_y_tol=ROW_Y_TOL):
    """cands: list of (box, score, label). 用中位數 y 當列位置錨點,濾掉離群的。"""
    if len(cands) <= 1:
        return cands
    ys = np.array([_center(c[0])[1] for c in cands])
    anchor = float(np.median(ys))
    return [c for c, y in zip(cands, ys) if abs(y - anchor) <= row_y_tol]


def _fit_slots(known, gap_typ=GAP_TYP, gap_reg_weight=GAP_REG_WEIGHT):
    """known: list of (x_center, y_center, w, h, score),已依 x 由小到大排序。
    回 (slot_assignment tuple, gap_local, cost)。

    K=2 時殘差項恆為 0(只有一段間距可比),任何「相鄰槽位」指派
    (0,1)/(1,2)/(2,3)的正則化代價也完全相同(gap_local 都等於實際間距,跟
    「哪一對相鄰」無關)—— 純用 (residual, reg) 排序會有平手,而平手時
    itertools.combinations 的枚舉順序會系統性偏向「最左邊那對」,不是真的
    沒有偏好。這裡加一個次要排序鍵:總外插距離(缺的槽位離最近已知槽位的
    槽位數總和),距離愈小代表兩個已知點愈「居中」、要外插的份量愈少,
    直覺上風險愈低,用它打破平手比固定選最左邊更合理。"""
    K = len(known)
    xs = [k[0] for k in known]
    best = None
    for assignment in itertools.combinations(range(4), K):
        s0, sK = assignment[0], assignment[-1]
        gap_local = (xs[-1] - xs[0]) / (sK - s0)
        residual = 0.0
        for j in range(K - 1):
            expected = gap_local * (assignment[j + 1] - assignment[j])
            actual = xs[j + 1] - xs[j]
            residual += (actual - expected) ** 2
        reg = gap_reg_weight * (gap_local - gap_typ) ** 2
        cost = residual + reg
        missing = [i for i in range(4) if i not in assignment]
        extrap_dist = sum(min(abs(i - s) for s in assignment) for i in missing)
        key = (cost, extrap_dist)
        if best is None or key < (best[2], best[3]):
            best = (assignment, gap_local, cost, extrap_dist)
    return best[0], best[1], best[2]


def _recover_weak(cx_expected, y_row, gap_local, used_boxes, weak_pool,
                  search_frac=WEAK_SEARCH_FRAC, row_y_tol=ROW_Y_TOL):
    """在缺格的預期位置附近,從「連主門檻都沒過」的弱候選裡找真正的偵測框,
    取代純幾何猜測。理由:主門檻(det_threshold)只是用來決定「算幾支」的
    保守估計,不代表低於門檻的 query 完全沒有訊號——round1 報告量過 recall
    只有 0.805,代表確實有一批箭頭的 query 分數沒過門檻但位置/類別其實是對
    的(信心校準差,不是定位/分類本身壞了)。找到的話用真實偵測框(位置準得
    多),找不到才退回純幾何合成框。

    weak_pool: list of (box, score, label),分數 >= WEAK_FLOOR、尚未被用掉的
    候選。回 (box, score) 或 (None, None)。"""
    half = max(search_frac * abs(gap_local), 10.0)
    best = None
    for box, score, label in weak_pool:
        if tuple(box) in used_boxes:  # numpy array 本身不可雜湊,不能直接 `in`
            continue
        cx, cy = _center(box)
        if abs(cx - cx_expected) > half or abs(cy - y_row) > row_y_tol:
            continue
        if best is None or score > best[1]:
            best = (box, score)
    return best if best is not None else (None, None)


def _infer_missing(known, gap_typ=GAP_TYP, gap_reg_weight=GAP_REG_WEIGHT,
                   weak_pool=None):
    """known: list of (box, score, label),len in {2,3}。回長度 4 的 slots list,
    每項 {'box':(x0,y0,x1,y1),'source':'det'|'det-weak'|'geom','score':float|None}。
    weak_pool 給的話,缺格會先嘗試「弱回收」(見 _recover_weak),回收不到才用
    純幾何合成框。"""
    known = sorted(known, key=lambda c: _center(c[0])[0])
    pts = []
    for box, score, label in known:
        cx, cy = _center(box)
        w, h = _box_wh(box)
        pts.append((cx, cy, w, h, score))
    assignment, gap_local, cost = _fit_slots(pts, gap_typ, gap_reg_weight)
    x0, s0 = pts[0][0], assignment[0]
    y_row = float(np.median([p[1] for p in pts]))
    w_typ = float(np.median([p[2] for p in pts]))
    h_typ = float(np.median([p[3] for p in pts]))
    used_boxes = {tuple(b) for b, _, _ in known}

    slots = [None] * 4
    for idx, slot_i in enumerate(assignment):
        cx, cy, w, h, score = pts[idx]
        slots[slot_i] = {"box": (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                          "source": "det", "score": float(score)}
    for i in range(4):
        if slots[i] is None:
            cx = x0 + gap_local * (i - s0)
            weak_box, weak_score = (None, None)
            if weak_pool:
                weak_box, weak_score = _recover_weak(
                    cx, y_row, gap_local, used_boxes, weak_pool)
            if weak_box is not None:
                slots[i] = {"box": tuple(weak_box), "source": "det-weak",
                           "score": float(weak_score)}
                used_boxes.add(tuple(weak_box))
            else:
                slots[i] = {"box": (cx - w_typ / 2, y_row - h_typ / 2,
                                    cx + w_typ / 2, y_row + h_typ / 2),
                            "source": "geom", "score": None}
    return slots, assignment, gap_local, cost


def _select_best_four(cands, gap_typ=GAP_TYP, gap_reg_weight=GAP_REG_WEIGHT,
                      score_beta=SCORE_BETA, max_pool=MAX_POOL_FOR_SELECT):
    """cands: list of (box, score, label), len >= 4。回選中的 4 個(依 x 排序)。"""
    pool = sorted(cands, key=lambda c: -c[1])[:max_pool]
    best = None
    for combo in itertools.combinations(pool, 4):
        combo = sorted(combo, key=lambda c: _center(c[0])[0])
        xs = [_center(c[0])[0] for c in combo]
        gap_local = (xs[3] - xs[0]) / 3.0
        residual = sum((xs[i + 1] - xs[i] - gap_local) ** 2 for i in range(3))
        reg = gap_reg_weight * (gap_local - gap_typ) ** 2
        score_sum = sum(c[1] for c in combo)
        cost = residual + reg - score_beta * score_sum
        if best is None or cost < best[1]:
            best = (combo, cost)
    combo = best[0]
    slots = []
    for box, score, label in combo:
        slots.append({"box": tuple(box), "source": "det", "score": float(score)})
    return slots


def geometric_complete(boxes, scores, labels, det_threshold=DEFAULT_DET_THRESHOLD,
                       row_y_tol=ROW_Y_TOL, gap_typ=GAP_TYP,
                       gap_reg_weight=GAP_REG_WEIGHT, score_beta=SCORE_BETA,
                       max_pool=MAX_POOL_FOR_SELECT, weak_floor=WEAK_FLOOR):
    """boxes: (N,4) xyxy ndarray, scores/labels: (N,) ndarray。RT-DETR 原始輸出
    (threshold=0 取全部 query 的結果),這裡自己套 det_threshold 篩選 + 補全。

    回 dict:
        status: 'ok' | 'fail'
        branch: '4' | '3->1' | '2->2' | 'fail_leq1'
        n_detected: 篩選並做完列過濾後,參與補全的候選數
        slots: status=='ok' 時,長度 4、依 x 排序的 list,每項
               {'box':(x0,y0,x1,y1), 'source':'det'|'det-weak'|'geom',
                'score':float|None}(見 _recover_weak 說明 'det-weak' 是什麼)
               status=='fail' 時是 None
    """
    keep = scores >= det_threshold
    cands = [(boxes[i], float(scores[i]), int(labels[i]))
             for i in range(len(boxes)) if keep[i]]
    if len(cands) <= 1:
        return {"status": "fail", "branch": BRANCH_FAIL, "n_detected": len(cands),
                "slots": None}

    row_cands = _row_filter(cands, row_y_tol)
    n = len(row_cands)
    if n <= 1:
        return {"status": "fail", "branch": BRANCH_FAIL, "n_detected": n,
                "slots": None}

    # 缺格補全(2/3 支分支)用的弱回收候選池:分數 >= weak_floor 的「全部」候選
    # (不受 det_threshold 限制),見 _recover_weak。
    weak_keep = scores >= weak_floor
    weak_pool = [(boxes[i], float(scores[i]), int(labels[i]))
                for i in range(len(boxes)) if weak_keep[i]]

    if n == 2:
        slots, *_ = _infer_missing(row_cands, gap_typ, gap_reg_weight, weak_pool)
        return {"status": "ok", "branch": BRANCH_2TO2, "n_detected": n,
                "slots": slots}

    if n == 3:
        slots, *_ = _infer_missing(row_cands, gap_typ, gap_reg_weight, weak_pool)
        return {"status": "ok", "branch": BRANCH_3TO1, "n_detected": n,
                "slots": slots}

    # n >= 4
    slots = _select_best_four(row_cands, gap_typ, gap_reg_weight, score_beta, max_pool)
    return {"status": "ok", "branch": BRANCH_4, "n_detected": n, "slots": slots}


if __name__ == "__main__":
    # 重新算一次檔案開頭引用的經驗統計值,供人工核對常數是否過時。
    import json
    ds_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "rune_dataset")
    with open(os.path.join(ds_dir, "detr_annotations_full.json"), encoding="utf-8") as f:
        data = json.load(f)
    gaps, yspans, widths, heights = [], [], [], []
    n_full4 = 0
    for r in data["records"]:
        bs = r["boxes"]
        if len(bs) != 4:
            continue
        n_full4 += 1
        cs = sorted([((b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2,
                     b["x1"] - b["x0"], b["y1"] - b["y0"]) for b in bs],
                   key=lambda t: t[0])
        xs = [c[0] for c in cs]
        ys = [c[1] for c in cs]
        gaps.extend(xs[i + 1] - xs[i] for i in range(3))
        yspans.append(max(ys) - min(ys))
        widths.extend(c[2] for c in cs)
        heights.extend(c[3] for c in cs)
    gaps, yspans = np.array(gaps), np.array(yspans)
    widths, heights = np.array(widths), np.array(heights)
    print(f"n images with all 4 boxes: {n_full4}/{len(data['records'])}")
    print(f"gap median {np.median(gaps):.2f} std {gaps.std():.2f} "
          f"p10 {np.percentile(gaps,10):.2f} p90 {np.percentile(gaps,90):.2f}")
    print(f"row y-span median {np.median(yspans):.2f} std {yspans.std():.2f} "
          f"p90 {np.percentile(yspans,90):.2f}")
    print(f"box width median {np.median(widths):.2f}  height median {np.median(heights):.2f}")
