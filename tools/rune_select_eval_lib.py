"""共用的評估小工具,供 tune_rune_select.py / eval_rune_select.py 使用。

只讀取 rune_select_cache_build.py 產生的快取(boxes/scores/labels + GT),
不碰 GPU、不改動既有檔案。重用 tools/eval_rune_detr.py 的 iou()。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from eval_rune_detr import iou  # noqa: E402


def load_cache(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def filter_by_filelist(cache_records, filelist):
    fs = set(filelist)
    return [r for r in cache_records if r["file"] in fs]


def files_of_annotation(ann_path):
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    return [r["file"] for r in data["records"]]


def greedy_match_all4(selected_boxes, gt_boxes, iou_thresh=0.5):
    """selected_boxes / gt_boxes: list of (x0,y0,x1,y1)。
    貪婪一對一配對(依 IoU 高到低),回傳是否「4 個都配對成功」
    (要求 len(selected)==len(gt)==4,且能湊滿 4 對 IoU>=thresh 的一對一配對)。"""
    if selected_boxes is None:
        return False
    if len(selected_boxes) != 4 or len(gt_boxes) != 4:
        return False
    pairs = []
    for si, sb in enumerate(selected_boxes):
        for gi, gb in enumerate(gt_boxes):
            v = iou(sb, gb)
            if v >= iou_thresh:
                pairs.append((v, si, gi))
    pairs.sort(key=lambda t: -t[0])
    used_s, used_g = set(), set()
    n_matched = 0
    for v, si, gi in pairs:
        if si in used_s or gi in used_g:
            continue
        used_s.add(si)
        used_g.add(gi)
        n_matched += 1
    return n_matched == 4


def recall_at_threshold(records, min_score, iou_thresh=0.5):
    """指標 1:recall / precision,與選擇規則無關(用所有 >=min_score 的框)。"""
    n_tp, n_pred, n_gt = 0, 0, 0
    n_exact4 = 0
    for r in records:
        boxes = r["boxes"]
        scores = r["scores"]
        gt_boxes = r["gt_boxes"]
        kept = [(b, s) for b, s in zip(boxes, scores) if s >= min_score]
        n_pred += len(kept)
        n_gt += len(gt_boxes)
        if len(kept) == 4:
            n_exact4 += 1
        used_gt = set()
        kept_sorted = sorted(kept, key=lambda bs: -bs[1])
        for b, s in kept_sorted:
            best_j, best_iou = -1, iou_thresh
            for j, gb in enumerate(gt_boxes):
                if j in used_gt:
                    continue
                v = iou(b, gb)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                used_gt.add(best_j)
                n_tp += 1
    precision = n_tp / max(1, n_pred)
    recall = n_tp / max(1, n_gt)
    return dict(precision=precision, recall=recall, n_pred=n_pred, n_gt=n_gt,
                n_tp=n_tp, n_exact4=n_exact4, n_img=len(records))


def evaluate_selection(records, select_fn, params, iou_thresh=0.5):
    """對每張圖跑 select_fn(boxes, scores, params),回傳:
    - n_img, n_abandoned(選不出 4 支)
    - all4_correct(以整個樣本集為分母,選不出來算失敗)
    - 選中的框(給 e2e 用),以及每張圖的耗時
    """
    n_img = len(records)
    n_abandoned = 0
    n_all4_correct = 0
    per_image = []
    for r in records:
        sel = select_fn(r["boxes"], r["scores"], params)
        if sel is None:
            n_abandoned += 1
            per_image.append(dict(file=r["file"], sel=None, all4_ok=False))
            continue
        ok = greedy_match_all4(sel, r["gt_boxes"], iou_thresh)
        if ok:
            n_all4_correct += 1
        per_image.append(dict(file=r["file"], sel=sel, all4_ok=ok))
    return dict(
        n_img=n_img, n_abandoned=n_abandoned,
        all4_correct_rate=n_all4_correct / max(1, n_img),
        abandon_rate=n_abandoned / max(1, n_img),
        per_image=per_image,
    )
