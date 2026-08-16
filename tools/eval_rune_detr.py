"""評估微調後的 RT-DETR 箭頭偵測器,在驗證集上量五項指標(對照 detr-detect.md 規格):

1. 偵測品質:每張圖是否剛好偵測到 4 支箭頭(precision/recall/平均偵測數)
2. 定位精度(最重要):偵測框中心 vs 真實箭頭中心距離,累積涵蓋率 <=2/4/6/8/12px、中位數、p90
3. 方向分類正確率(偵測器自己的類別輸出)
4. 端到端:用偵測到的 4 支箭頭圍出區域,跑既有 rune_cv._chroma_map/_seg/_direction_tpl,
   報單支正確率與四支全對率(這是最終真正在意的數字)
5. 推論耗時(單張圖,GPU)

不改動 rune_cv.py / 任何既有檔案,只讀取、只呼叫既有函式。
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch
from PIL import Image
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_cv  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_rune_detr import DS_DIR, DIRS, load_split  # noqa: E402


def load_model(model_dir, device, ckpt=None):
    from transformers import RTDetrForObjectDetection, RTDetrImageProcessor
    if ckpt:
        from train_rune_detr import build_model, MODEL_ID
        model = build_model()
        state = torch.load(ckpt, map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        processor = RTDetrImageProcessor.from_pretrained(MODEL_ID)
        print(f"從 checkpoint 讀取(epoch {state['epoch'] + 1}, loss {state['loss']:.4f})")
    else:
        model = RTDetrForObjectDetection.from_pretrained(model_dir)
        processor = RTDetrImageProcessor.from_pretrained(model_dir)
    model.to(device).eval()
    return model, processor


def infer(model, processor, bgr, device, threshold):
    rgb = bgr[:, :, ::-1]
    img = Image.fromarray(np.ascontiguousarray(rgb))
    enc = processor(images=[img], return_tensors="pt").to(device)
    t0 = time.perf_counter()
    with torch.no_grad():
        out = model(pixel_values=enc["pixel_values"])
    if device.type == "cuda":
        torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    h, w = bgr.shape[:2]
    # threshold 只在這裡先粗篩(避免把明顯背景的 300 個 query 全部帶下去算),
    # 真正的偵測品質門檻另外套用,見 main() 的 --min-score。
    res = processor.post_process_object_detection(
        out, threshold=threshold, target_sizes=[(h, w)])[0]
    boxes = res["boxes"].cpu().numpy()   # xyxy, 原圖像素座標
    scores = res["scores"].cpu().numpy()
    labels = res["labels"].cpu().numpy()
    return boxes, scores, labels, dt


def center(b):
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def end_to_end_dirs(bgr, det_boxes_sorted):
    """用偵測到的箭頭框圍出區域,跑既有色度分割 + 模板判向。
    det_boxes_sorted: 依 x 中心由左到右排序的最多 4 個 (x0,y0,x1,y1)。
    回長度與輸入相同的方向 list(讀不到的位置是 None)。"""
    if not det_boxes_sorted:
        return []
    pad = 6
    x0 = max(0, int(min(b[0] for b in det_boxes_sorted)) - pad)
    y0 = max(0, int(min(b[1] for b in det_boxes_sorted)) - pad)
    x1 = min(bgr.shape[1], int(max(b[2] for b in det_boxes_sorted)) + pad)
    y1 = min(bgr.shape[0], int(max(b[3] for b in det_boxes_sorted)) + pad)
    cap = bgr[y0:y1, x0:x1]
    if cap.size == 0:
        return [None] * len(det_boxes_sorted)
    dist = rune_cv._chroma_map(cap)
    dirs = []
    for b in det_boxes_sorted:
        sx0 = max(0, int(b[0]) - x0 - pad // 2)
        sx1 = min(cap.shape[1], int(b[2]) - x0 + pad // 2)
        if sx1 <= sx0:
            dirs.append(None)
            continue
        m = rune_cv._seg(dist, sx0, sx1)
        if m is None:
            dirs.append(None)
            continue
        dirs.append(rune_cv._direction_tpl(m) or rune_cv._direction(m))
    return dirs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models", "rune_detr", "final"))
    ap.add_argument("--min-score", type=float, default=0.05,
                    help="指標 1(偵測品質)用的信心門檻。RT-DETR 是一對一匹配"
                         "(Hungarian),不需要 NMS,300 個 query 裡真正匹配到物件的"
                         "與純背景的 query 分數天生就有差距,不代表 sigmoid 值本身"
                         "要 >0.5 才算數 —— 門檻應該取在分數斷層處,不是固定 0.5。")
    ap.add_argument("--ckpt", default=None, help="從 checkpoint.pt(訓練中途存檔)"
                    "讀取,供分段訓練期間監控用;不填就讀 --model-dir 的 HF 權重。")
    ap.add_argument("--ann", default=os.path.join(DS_DIR, "detr_annotations.json"))
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,依規則不自動退回 CPU,中止。")
        sys.exit(1)

    model, processor = load_model(args.model_dir, device, ckpt=args.ckpt)
    _, val_recs = load_split(ann_path=args.ann)
    print(f"驗證集 {len(val_recs)} 張圖,min-score(指標1)={args.min_score}")

    # ---------- 逐張推論 ----------
    n_img = 0
    n_pred_total = 0
    n_gt_total = 0
    n_tp_iou50 = 0             # 偵測品質用:predicted 與某個 GT IoU>=0.5
    n_exact4 = 0                # 剛好偵測到 4 支的圖數
    center_dists = []           # 定位精度:matched pair 的中心距離(max(|dx|,|dy|))
    n_missed_gt = 0             # 沒有任何偵測配對到的 GT(視為定位失敗,計入 >12px)
    cls_correct = 0
    cls_total = 0
    e2e_single_ok = 0
    e2e_single_tot = 0
    e2e_exact = 0
    e2e_attempted = 0           # 湊得出 4 支候選框(不論對錯)的圖數
    timings = []

    for ri, rec in enumerate(val_recs):
        bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
        if bgr is None:
            continue
        # 拿全部原始輸出(threshold=0),指標 2/3/4 一律用「信心最高的 4 個」,
        # 與絕對信心值是否 >0.5 無關;指標 1 才套用 --min-score。
        boxes, scores, labels, dt = infer(model, processor, bgr, device, 0.0)
        if ri >= 3:   # 前 3 張當 GPU warm-up,不計入計時
            timings.append(dt)
        n_img += 1

        gt = rec["boxes"]  # 已經是 x 由左到右排序(遍歷順序即 slot k=0..3)
        n_gt_total += len(gt)
        keep = scores >= args.min_score
        boxes_kept, scores_kept = boxes[keep], scores[keep]
        n_pred_total += len(boxes_kept)
        if len(boxes_kept) == 4:
            n_exact4 += 1

        # 偵測品質:IoU>=0.5 的一對一匹配(貪婪,依 IoU 高到低)
        gt_boxes = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in gt]
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
                n_tp_iou50 += 1

        # 定位精度 + 分類正確率:用「信心最高的至多 4 個偵測」與 4 個 GT 做中心距離
        # 最佳匹配(Hungarian),配不到的 GT 記為 miss。
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

        # 端到端:取信心最高的 4 個偵測(不足 4 個就整張算失敗),依 x 排序後
        # 圍出區域跑既有色度分割 + 模板判向,與 GT 方向比較(GT 依 slot 順序排列)。
        if len(boxes) >= 4:
            top4_idx = np.argsort(-scores)[:4]
            det4 = boxes[top4_idx]
            order = np.argsort(det4[:, 0] + det4[:, 2])  # 依中心 x 排序
            det4_sorted = [tuple(det4[o]) for o in order]
            pred_dirs = end_to_end_dirs(bgr, det4_sorted)
            truth_dirs = rec["dirs"] if "dirs" in rec else [b["label"] for b in gt]
            e2e_attempted += 1
            ok = [p == t for p, t in zip(pred_dirs, truth_dirs)]
            e2e_single_ok += sum(ok)
            e2e_single_tot += len(truth_dirs)
            e2e_exact += all(ok) and len(ok) == len(truth_dirs)

    # ---------- 彙總 ----------
    print("\n===== 1. 偵測品質 =====")
    precision = n_tp_iou50 / max(1, n_pred_total)
    recall = n_tp_iou50 / max(1, n_gt_total)
    print(f"precision(IoU>=0.5) {precision:.3f}  recall {recall:.3f}  "
          f"平均每張偵測數 {n_pred_total / max(1, n_img):.2f}  "
          f"剛好 4 支的圖 {n_exact4}/{n_img} ({n_exact4 / max(1, n_img):.1%})")

    print("\n===== 2. 定位精度(matched pair, max(|dx|,|dy|)) =====")
    all_d = np.array(center_dists + [999.0] * n_missed_gt)  # 沒配到的 GT 算失敗
    n_d = len(all_d)
    for th in (2, 4, 6, 8, 12):
        cov = (all_d <= th).mean() if n_d else 0
        print(f"  <= {th:>2}px: {cov:.1%}")
    print(f"  > 12px (含 {n_missed_gt} 個沒配到偵測的 GT): {(all_d > 12).mean():.1%}")
    if n_d:
        finite = np.array(center_dists) if center_dists else np.array([np.nan])
        print(f"  中位數(僅 matched) {np.median(finite):.2f}px   "
              f"p90(僅 matched) {np.percentile(finite, 90):.2f}px")
        print(f"  中位數(含 miss 記 999) {np.median(all_d):.2f}px   "
              f"p90(含 miss) {np.percentile(all_d, 90):.2f}px")

    print("\n===== 3. 方向分類正確率(偵測器類別輸出,matched pair) =====")
    print(f"  {cls_correct}/{cls_total} = {cls_correct / max(1, cls_total):.1%}")

    print("\n===== 4. 端到端(偵測框圍區域 -> 既有色度分割/模板判向) =====")
    print(f"  湊得出 4 支候選框的圖: {e2e_attempted}/{n_img}")
    print(f"  單支正確率: {e2e_single_ok}/{e2e_single_tot} = "
          f"{e2e_single_ok / max(1, e2e_single_tot):.1%}")
    print(f"  四支全對率(以嘗試過的 {e2e_attempted} 張為分母): "
          f"{e2e_exact}/{e2e_attempted} = {e2e_exact / max(1, e2e_attempted):.1%}")
    print(f"  四支全對率(以整個驗證集 {n_img} 張為分母,湊不出 4 支就算失敗): "
          f"{e2e_exact}/{n_img} = {e2e_exact / max(1, n_img):.1%}")

    print("\n===== 5. 推論耗時(GPU,不含前 3 張 warm-up) =====")
    if timings:
        t = np.array(timings) * 1000
        print(f"  平均 {t.mean():.1f}ms  中位數 {np.median(t):.1f}ms  "
              f"min {t.min():.1f}ms  max {t.max():.1f}ms  (n={len(t)})")


if __name__ == "__main__":
    main()
