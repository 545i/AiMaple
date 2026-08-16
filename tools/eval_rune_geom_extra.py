"""補 eval_rune_geom.py 沒有的三項:定位精度(涵蓋率/中位誤差)、偵測器自己的方向
分類正確率、CPU 推論耗時。兩個樣本集的定義與 eval_rune_geom.py 完全一致(blob_n>=10
全集 246 張、blob_n>=0 全集 364 張,都不限 val split),方便跟 detr-aspect.md 的
對照基準(squash 640x640)並列比較。

只讀取、只呼叫既有函式(eval_rune_detr.py 的 load_model/infer/center),不改動
任何既有檔案。GPU 耗時 eval_rune_geom.py 已經有了,這裡只補 CPU。
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_rune_detr import load_model, infer, center, DIRS  # noqa: E402
from eval_rune_geom import load_all_records, DS_DIR  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_cv  # noqa: E402


def eval_localization_and_cls(model, processor, device, records, tag):
    center_dists = []
    n_missed_gt = 0
    cls_correct = 0
    cls_total = 0
    for rec in records:
        bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
        if bgr is None:
            continue
        boxes, scores, labels, _dt = infer(model, processor, bgr, device, 0.0)
        gt = rec["boxes"]
        gt_boxes = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in gt]
        top = np.argsort(-scores)[:4]
        pred_sub = boxes[top]
        pred_lab = labels[top]
        if len(pred_sub) > 0 and len(gt_boxes) > 0:
            cost = np.zeros((len(gt_boxes), len(pred_sub)))
            for gi, gb in enumerate(gt_boxes):
                gc = center(gb)
                for pj, pb in enumerate(pred_sub):
                    pc = center(pb)
                    cost[gi, pj] = max(abs(gc[0] - pc[0]), abs(gc[1] - pc[1]))
            row, col = linear_sum_assignment(cost)
            matched_gt = set()
            for gi, pj in zip(row, col):
                d = cost[gi, pj]
                center_dists.append(d)
                matched_gt.add(gi)
                cls_total += 1
                if DIRS[pred_lab[pj]] == gt[gi]["label"]:
                    cls_correct += 1
            n_missed_gt += len(gt_boxes) - len(matched_gt)
        else:
            n_missed_gt += len(gt_boxes)

    all_d = np.array(center_dists + [999.0] * n_missed_gt)
    n_d = len(all_d)
    print(f"\n========== {tag}:定位精度 + 分類正確率 ==========")
    for th in (2, 4, 6, 8, 12):
        cov = (all_d <= th).mean() if n_d else 0
        print(f"  <= {th:>2}px: {cov:.1%}")
    print(f"  > 12px (含 {n_missed_gt} 個沒配到偵測的 GT): {(all_d > 12).mean():.1%}")
    if center_dists:
        finite = np.array(center_dists)
        print(f"  中位數(僅 matched) {np.median(finite):.2f}px   "
              f"p90(僅 matched) {np.percentile(finite, 90):.2f}px")
        print(f"  中位數(含 miss 記 999) {np.median(all_d):.2f}px")
    print(f"  方向分類正確率(偵測器類別輸出,matched pair): "
          f"{cls_correct}/{cls_total} = {cls_correct / max(1, cls_total):.1%}")


def eval_cpu_timing(model_dir, ckpt, records, n_images, tag):
    device = torch.device("cpu")
    model, processor = load_model(model_dir, device, ckpt=ckpt)
    timings = []
    for ri, rec in enumerate(records[:n_images]):
        bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
        if bgr is None:
            continue
        _boxes, _scores, _labels, dt = infer(model, processor, bgr, device, 0.0)
        if ri >= 3:
            timings.append(dt)
    if timings:
        t = np.array(timings) * 1000
        print(f"\n========== {tag}:CPU 推論耗時(不含前 3 張 warm-up,n={len(t)}) ==========")
        print(f"  平均 {t.mean():.1f}ms  中位數 {np.median(t):.1f}ms  "
              f"min {t.min():.1f}ms  max {t.max():.1f}ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models", "rune_detr_ar", "final"))
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--cpu-n", type=int, default=30,
                    help="CPU 計時只跑前 N 張(CPU 推論慢,不需要全跑),見上方 tag。")
    ap.add_argument("--skip-cpu", action="store_true")
    args = ap.parse_args()

    ann_gate = os.path.join(DS_DIR, "detr_annotations.json")
    ann_full = os.path.join(DS_DIR, "detr_annotations_full.json")
    recs_gate = load_all_records(ann_gate)
    recs_full = load_all_records(ann_full)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        model, processor = load_model(args.model_dir, device, ckpt=args.ckpt)
        eval_localization_and_cls(model, processor, device, recs_gate,
                                  "blob_n>=10, 全集(246 張)")
        eval_localization_and_cls(model, processor, device, recs_full,
                                  "blob_n>=0, 全集(364 張)")
        del model
        torch.cuda.empty_cache()
    else:
        print("!!! CUDA 不可用,跳過定位精度/分類正確率(需要 GPU 版評測對齊既有流程)。")

    if not args.skip_cpu:
        eval_cpu_timing(args.model_dir, args.ckpt, recs_gate, args.cpu_n,
                        "blob_n>=10")
        eval_cpu_timing(args.model_dir, args.ckpt, recs_full, args.cpu_n,
                        "blob_n>=0")


if __name__ == "__main__":
    main()
