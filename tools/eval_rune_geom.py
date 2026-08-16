"""量幾何補全(tools/rune_geom_complete.py)前後的效果。

重用 tools/eval_rune_detr.py 既有、已驗證過的函式(load_model / infer / iou /
end_to_end_dirs),不重寫、不改動那個檔案 —— 「補全前」的數字要跟 round 1 報告
(.superpowers/detr-detect.md)完全可比。

跑兩個樣本集(都是圖片檔名的全集,不只 val split):
    1. blob_n>=10(rune_dataset/detr_annotations.json,246 張圖,round 1 用的集合)
    2. blob_n>=0 (rune_dataset/detr_annotations_full.json,364 張圖,任務要求的
       「全部有真值的樣本」,用來跟現行線上流程的 41% 基準做真正對等的比較)

兩個集合都評全部圖(不是只評 val split)—— blob_n>=10 的 246 張裡有 195 張是這次
訓練的訓練集,所以這個「全集」數字會比純 val(51 張)樂觀,報告裡會分開列出
純 val 51 張的數字(呼叫時用 --val-only 開關)。
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_rune_detr import load_model, infer, iou, center, end_to_end_dirs  # noqa: E402
from rune_geom_complete import geometric_complete, DEFAULT_DET_THRESHOLD  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_cv  # noqa: E402

DS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rune_dataset")


def load_all_records(ann_path):
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["records"]


def load_val_only_records(ann_path):
    """只回 detr_split.json 裡 val 那批(跟 round1 報告的 51 張驗證集一致)。"""
    from train_rune_detr import load_split
    _, val_recs = load_split(ann_path=ann_path)
    return val_recs


def eval_set(model, processor, device, records, min_score, tag):
    n_img = 0
    n_gt_total = 0

    # ---- 補全前(=eval_rune_detr.py 的既有邏輯,原樣重現) ----
    n_tp_before = 0
    n_pred_kept_total = 0
    n_exact4_before = 0
    e2e_ok_before, e2e_tot_before, e2e_exact_before, e2e_attempted_before = 0, 0, 0, 0

    # ---- 補全後 ----
    branch_counts = {"4": 0, "3->1": 0, "2->2": 0, "fail_leq1": 0}
    branch_e2e_ok = {k: 0 for k in branch_counts}
    branch_e2e_tot = {k: 0 for k in branch_counts}
    branch_e2e_exact = {k: 0 for k in branch_counts}
    branch_e2e_n = {k: 0 for k in branch_counts}
    n_tp_after = 0
    n_recall_after_denom = 0
    e2e_ok_after, e2e_tot_after, e2e_exact_after, e2e_attempted_after = 0, 0, 0, 0

    timings = []

    for ri, rec in enumerate(records):
        bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
        if bgr is None:
            continue
        boxes, scores, labels, dt = infer(model, processor, bgr, device, 0.0)
        if ri >= 3:
            timings.append(dt)
        n_img += 1

        gt = rec["boxes"]
        gt_boxes = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in gt]
        gt_labels = [b["label"] for b in gt]
        n_gt_total += len(gt_boxes)

        # ---------- 補全前 ----------
        keep = scores >= min_score
        boxes_kept, scores_kept = boxes[keep], scores[keep]
        n_pred_kept_total += len(boxes_kept)
        if len(boxes_kept) == 4:
            n_exact4_before += 1
        used_gt = set()
        for pi in np.argsort(-scores_kept):
            best_j, best_iou = -1, 0.5
            for j, gb in enumerate(gt_boxes):
                if j in used_gt:
                    continue
                v = iou(boxes_kept[pi], gb)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                used_gt.add(best_j)
                n_tp_before += 1

        if len(boxes) >= 4:
            top4_idx = np.argsort(-scores)[:4]
            det4 = boxes[top4_idx]
            order = np.argsort(det4[:, 0] + det4[:, 2])
            det4_sorted = [tuple(det4[o]) for o in order]
            pred_dirs = end_to_end_dirs(bgr, det4_sorted)
            e2e_attempted_before += 1
            ok = [p == t for p, t in zip(pred_dirs, gt_labels)]
            e2e_ok_before += sum(ok)
            e2e_tot_before += len(ok)
            e2e_exact_before += int(all(ok) and len(ok) == len(gt_labels))

        # ---------- 補全後 ----------
        result = geometric_complete(boxes, scores, labels, det_threshold=min_score)
        branch = result["branch"]
        branch_counts[branch] += 1
        if result["status"] == "ok":
            slots = result["slots"]
            det4_sorted = [s["box"] for s in slots]
            # recall-after:只在 gt 剛好 4 支(絕大多數情況)時逐槽位對齊比 IoU,
            # 少數 gt<4(分割失敗漏格,全體集合裡機率 <0.3%)的圖跳過,見檔頭說明。
            if len(gt_boxes) == 4:
                n_recall_after_denom += 4
                for i in range(4):
                    if iou(det4_sorted[i], gt_boxes[i]) >= 0.5:
                        n_tp_after += 1
            pred_dirs = end_to_end_dirs(bgr, det4_sorted)
            ok = [p == t for p, t in zip(pred_dirs, gt_labels)]
            e2e_attempted_after += 1
            e2e_ok_after += sum(ok)
            e2e_tot_after += len(ok)
            exact_ok = int(all(ok) and len(ok) == len(gt_labels))
            e2e_exact_after += exact_ok

            branch_e2e_n[branch] += 1
            branch_e2e_ok[branch] += sum(ok)
            branch_e2e_tot[branch] += len(ok)
            branch_e2e_exact[branch] += exact_ok

    print(f"\n========== {tag}(n={n_img} 張圖,gt 箭頭總數 {n_gt_total}) ==========")
    print("--- 補全前(既有 eval_rune_detr.py 邏輯)---")
    print(f"  recall(IoU>=0.5) {n_tp_before / max(1, n_gt_total):.3f}   "
          f"剛好偵測到 4 支 {n_exact4_before}/{n_img} ({n_exact4_before / max(1, n_img):.1%})")
    print(f"  端到端(湊得出 4 支候選的 {e2e_attempted_before}/{n_img} 張):"
          f"單支 {e2e_ok_before}/{e2e_tot_before} = {e2e_ok_before / max(1, e2e_tot_before):.1%}   "
          f"四支全對(分母=嘗試過) {e2e_exact_before}/{max(1, e2e_attempted_before)} = "
          f"{e2e_exact_before / max(1, e2e_attempted_before):.1%}   "
          f"四支全對(分母=全部 {n_img} 張) {e2e_exact_before / max(1, n_img):.1%}")

    print("--- 補全後(幾何補全)---")
    print(f"  recall(IoU>=0.5,僅 gt 剛好 4 支的圖,n={n_recall_after_denom}) "
          f"{n_tp_after / max(1, n_recall_after_denom):.3f}")
    print(f"  湊出 4 支(status==ok)的圖:{sum(branch_counts[k] for k in ('4','3->1','2->2'))}/{n_img} "
          f"({sum(branch_counts[k] for k in ('4','3->1','2->2')) / max(1, n_img):.1%})")
    print(f"  端到端(湊得出 4 支候選的 {e2e_attempted_after}/{n_img} 張):"
          f"單支 {e2e_ok_after}/{e2e_tot_after} = {e2e_ok_after / max(1, e2e_tot_after):.1%}   "
          f"四支全對(分母=嘗試過) {e2e_exact_after}/{max(1, e2e_attempted_after)} = "
          f"{e2e_exact_after / max(1, e2e_attempted_after):.1%}   "
          f"四支全對(分母=全部 {n_img} 張) {e2e_exact_after / max(1, n_img):.1%}")

    print("--- 補全後,分支別統計 ---")
    for k in ("4", "3->1", "2->2", "fail_leq1"):
        n_b = branch_counts[k]
        if k == "fail_leq1":
            print(f"  分支 {k}: {n_b} 張(<=1 支候選,放棄,無法算端到端)")
            continue
        tot = branch_e2e_tot[k]
        print(f"  分支 {k}: {n_b} 張   單支正確率 {branch_e2e_ok[k]}/{max(1, tot)} = "
              f"{branch_e2e_ok[k] / max(1, tot):.1%}   四支全對率 "
              f"{branch_e2e_exact[k]}/{max(1, branch_e2e_n[k])} = "
              f"{branch_e2e_exact[k] / max(1, branch_e2e_n[k]):.1%}")

    if timings:
        t = np.array(timings) * 1000
        print(f"--- 推論耗時(GPU): 平均 {t.mean():.1f}ms 中位數 {np.median(t):.1f}ms "
              f"(n={len(t)}) ---")

    return {
        "tag": tag, "n_img": n_img, "n_gt_total": n_gt_total,
        "before": {"recall": n_tp_before / max(1, n_gt_total),
                  "exact4": n_exact4_before / max(1, n_img),
                  "e2e_single": e2e_ok_before / max(1, e2e_tot_before),
                  "e2e_exact_attempted": e2e_exact_before / max(1, e2e_attempted_before),
                  "e2e_exact_all": e2e_exact_before / max(1, n_img)},
        "after": {"recall": n_tp_after / max(1, n_recall_after_denom),
                 "status_ok_rate": sum(branch_counts[k] for k in ("4", "3->1", "2->2")) / max(1, n_img),
                 "e2e_single": e2e_ok_after / max(1, e2e_tot_after),
                 "e2e_exact_attempted": e2e_exact_after / max(1, e2e_attempted_after),
                 "e2e_exact_all": e2e_exact_after / max(1, n_img),
                 "branch_counts": branch_counts,
                 "branch_e2e_single": {k: branch_e2e_ok[k] / max(1, branch_e2e_tot[k]) for k in branch_counts if k != "fail_leq1"},
                 "branch_e2e_exact": {k: branch_e2e_exact[k] / max(1, branch_e2e_n[k]) for k in branch_counts if k != "fail_leq1"}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models", "rune_detr", "final"))
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--min-score", type=float, default=DEFAULT_DET_THRESHOLD)
    ap.add_argument("--val-only", action="store_true",
                    help="blob_n>=10 集合只評 detr_split.json 的 val 那 51 張(與"
                         "round1 報告完全對齊),不加這個旗標就評整個 246 張。")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,依規則不自動退回 CPU,中止。")
        sys.exit(1)

    model, processor = load_model(args.model_dir, device, ckpt=args.ckpt)

    ann_gate = os.path.join(DS_DIR, "detr_annotations.json")
    ann_full = os.path.join(DS_DIR, "detr_annotations_full.json")

    if args.val_only:
        recs_gate = load_val_only_records(ann_gate)
        tag_gate = "blob_n>=10, val-only(51 張,與 round1 報告對齊)"
    else:
        recs_gate = load_all_records(ann_gate)
        tag_gate = "blob_n>=10, 全集(246 張,含訓練集)"
    recs_full = load_all_records(ann_full)
    tag_full = "blob_n>=0, 全部有真值樣本(364 張)"

    results = []
    results.append(eval_set(model, processor, device, recs_gate, args.min_score, tag_gate))
    results.append(eval_set(model, processor, device, recs_full, args.min_score, tag_full))

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".superpowers", "_eval_rune_geom_last.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n(結果另存 {out_path} 供寫報告用)")


if __name__ == "__main__":
    main()
