# -*- coding: utf-8 -*-
"""在 rune_collect/(新款細長旋轉箭頭,297 筆真實採集,真值來自
tools/rune_collect_dataset_build.py 的色度遮罩精修 + rune_wheel.angle_of)上,
比較兩個 RT-DETR 權重(--model-dir-a/--model-dir-b)的端到端表現。

方法與 tools/eval_rune_detr.py 一致(信心最高的候選框、IoU>=0.5 配對、GPU 推論),
差別只在最後一步的判向器:經典款用 rune_cv._chroma_map/_seg/_direction_tpl
(eval_rune_detr.py::end_to_end_dirs),新款箭頭顏色不循環、固定綠尾→黃→紅頭,
production 的判向器是 server/rune_wheel.py::angle_of() + nearest_cardinal()
(見 server/rune.py 呼叫端的旋轉分支)——這裡直接呼叫它,不重刻角度數學。

真值只有單一靜態幀(不是完整旋轉序列),所以這裡評的是「這一幀當下的瞬時方向
讀取」,不是完整的晃動偵測流程(server/rune_wheel.py::solve()那一段跑不動,因為
沒有連續多幀)——與標註產生時的方法完全對齊(都是單幀 angle_of),公平比較。

不改動 rune_dataset/、rune_collect/ 任何檔案,只讀取。
"""
import argparse
import json
import os
import sys

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, HERE)

import rune_cv  # noqa: E402
import rune_wheel  # noqa: E402
from eval_rune_detr import load_model, infer, iou  # noqa: E402

DS_DIR = os.path.join(ROOT, "rune_dataset")


def load_new_style_records(ann_path):
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    return [r for r in data["records"] if "rune_collect" in r["file"]]


def end_to_end_dirs_new_style(bgr, det_boxes_sorted):
    """新款判向:每個偵測框直接呼叫 rune_wheel.angle_of + nearest_cardinal。
    不像經典款那樣先圍出膠囊區域再切格——新款沒有膠囊,偵測框本身就是箭頭框。"""
    dirs = []
    h, w = bgr.shape[:2]
    for b in det_boxes_sorted:
        x0 = max(0, int(b[0]))
        y0 = max(0, int(b[1]))
        x1 = min(w, int(b[2]))
        y1 = min(h, int(b[3]))
        if x1 <= x0 or y1 <= y0:
            dirs.append(None)
            continue
        crop = bgr[y0:y1, x0:x1]
        ang = rune_wheel.angle_of(crop)
        dirs.append(rune_wheel.nearest_cardinal(ang) if ang is not None else None)
    return dirs


def eval_one_model(model_dir, records, device, min_score=0.05):
    model, processor = load_model(model_dir, device)

    n_img = 0
    n_gt_total = 0
    n_pred_total = 0
    n_tp_iou50 = 0
    single_ok = 0
    single_tot = 0
    n_record_all_correct = 0
    timings = []

    for ri, rec in enumerate(records):
        path = os.path.normpath(os.path.join(DS_DIR, rec["file"]))
        bgr = rune_cv.imread(path)
        if bgr is None:
            continue
        n_img += 1
        gt = rec["boxes"]
        n_gt_total += len(gt)

        boxes, scores, labels, dt = infer(model, processor, bgr, device, 0.0)
        if ri >= 3:
            timings.append(dt)

        keep = scores >= min_score
        boxes_kept = boxes[keep]
        n_pred_total += len(boxes_kept)
        gt_boxes = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in gt]
        used_gt = set()
        for pi in np.argsort(-scores[keep]):
            best_j, best_iou = -1, 0.5
            for j, gb in enumerate(gt_boxes):
                if j in used_gt:
                    continue
                v = iou(boxes_kept[pi], gb)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                used_gt.add(best_j)
                n_tp_iou50 += 1

        # 端到端:取信心最高的 len(gt) 個偵測(不足就用全部拿到的),依 x 排序後
        # 逐框呼叫 rune_wheel 判向,與 GT 標籤比較(GT 依 slot 順序,x 由左到右)。
        n_take = len(gt_boxes)
        if len(boxes) >= n_take and n_take > 0:
            top_idx = np.argsort(-scores)[:n_take]
            detN = boxes[top_idx]
            order = np.argsort(detN[:, 0] + detN[:, 2])
            detN_sorted = [tuple(detN[o]) for o in order]
            pred_dirs = end_to_end_dirs_new_style(bgr, detN_sorted)
            truth_dirs = [b["label"] for b in gt]
            ok = [p == t for p, t in zip(pred_dirs, truth_dirs)]
            single_ok += sum(ok)
            single_tot += len(truth_dirs)
            if all(ok) and len(ok) == len(truth_dirs):
                n_record_all_correct += 1
        else:
            single_tot += len(gt_boxes)

    recall = n_tp_iou50 / max(1, n_gt_total)
    precision = n_tp_iou50 / max(1, n_pred_total)
    t = np.array(timings) * 1000 if timings else np.array([0.0])

    return dict(
        model_dir=model_dir, n_img=n_img, n_gt_total=n_gt_total,
        recall=recall, precision=precision,
        single_ok=single_ok, single_tot=single_tot,
        single_rate=single_ok / max(1, single_tot),
        record_all_correct=n_record_all_correct,
        record_all_correct_rate=n_record_all_correct / max(1, n_img),
        infer_ms_mean=float(t.mean()), infer_ms_median=float(np.median(t)),
    )


def print_result(res, tag):
    print(f"\n===== {tag}({res['model_dir']}) =====")
    print(f"樣本數(圖) {res['n_img']}  GT箭頭數 {res['n_gt_total']}")
    print(f"偵測 recall(IoU>=0.5, min_score=0.05) {res['recall']:.1%}  "
          f"precision {res['precision']:.1%}")
    print(f"端到端單支正確率: {res['single_ok']}/{res['single_tot']} = {res['single_rate']:.1%}")
    print(f"每筆全對率(該記錄全部 GT 箭頭都判對,分母=全部 {res['n_img']} 張): "
          f"{res['record_all_correct']}/{res['n_img']} = {res['record_all_correct_rate']:.1%}")
    print(f"推論耗時 平均 {res['infer_ms_mean']:.1f}ms 中位數 {res['infer_ms_median']:.1f}ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir-a", required=True, help="現行線上模型(對照組)")
    ap.add_argument("--model-dir-b", required=True, help="新訓練模型")
    ap.add_argument("--ann", default=os.path.join(DS_DIR, "detr_annotations_mixed.json"))
    ap.add_argument("--min-score", type=float, default=0.05)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,依規則不自動退回 CPU,中止。")
        sys.exit(1)

    records = load_new_style_records(args.ann)
    print(f"新款樣本(rune_collect/): {len(records)} 張圖")

    res_a = eval_one_model(args.model_dir_a, records, device, args.min_score)
    print_result(res_a, "A(現行線上模型)")

    res_b = eval_one_model(args.model_dir_b, records, device, args.min_score)
    print_result(res_b, "B(新訓練模型)")

    print("\n===== 對照 =====")
    print(f"端到端單支正確率: A {res_a['single_rate']:.1%}  ->  B {res_b['single_rate']:.1%}")
    print(f"每筆全對率        : A {res_a['record_all_correct_rate']:.1%}  ->  "
          f"B {res_b['record_all_correct_rate']:.1%}")
    print(f"偵測 recall       : A {res_a['recall']:.1%}  ->  B {res_b['recall']:.1%}")


if __name__ == "__main__":
    main()
