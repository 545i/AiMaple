# -*- coding: utf-8 -*-
"""把偵測器的實際輸出畫出來:所有候選框 + 信心分數、幾何選擇挑中的 4 支、
判向結果與真值的對錯。

【為什麼需要】端到端只給一個正確率數字,看不出失敗是哪一段造成的:
    偵測沒抓到? 抓到了但幾何選擇挑錯? 選對了但判向讀錯?
這三種的修法完全不同。把三層都畫出來,一眼就能分辨。

【刻意呼叫 production 的函式,不重刻】前一輪吃過虧:評估腳本自己重刻了
「取分數最高 4 支」的框選,而 production 用的是幾何選擇,同一批圖量出來差了
10 個百分點(79.0% vs 89.4%)。重刻 production 邏輯 = 量到的不是真實系統。
所以這裡一律走 rune_detr / rune_cv 的既有函式。

用法:
    venv/Scripts/python.exe -X utf8 tools/viz_rune_detect.py \
        --images rune_synth/stage_e_0000.png --ann rune_synth/annotations_e.json \
        --out-dir <輸出目錄>
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import rune_cv      # noqa: E402
import rune_detr    # noqa: E402

# BGR
C_CAND = (140, 140, 140)    # 未被選中的候選框:灰
C_SEL_OK = (80, 220, 80)    # 被選中且判向正確:綠
C_SEL_BAD = (60, 60, 240)   # 被選中但判向錯誤:紅
C_GT = (255, 190, 60)       # 真值框:藍


def raw_detections(frame_bgr):
    """跑一次模型,回 (boxes_xyxy, scores) —— 全部候選,未經幾何選擇。

    走 rune_detr 自己的前處理/後處理,與 detect_arrows 內部完全同一條路徑,
    只是多回傳一份「選擇之前」的候選,供視覺化用。
    """
    sess = rune_detr._session()
    if sess is None:
        return None, None, None
    import torch
    model, device = sess
    h = frame_bgr.shape[0]
    by0, by1 = rune_cv.search_band(h)
    band = frame_bgr[by0:by1]
    x = rune_detr._preprocess(band)
    with torch.no_grad():
        out = model(pixel_values=torch.from_numpy(x).to(device))
    boxes, scores = rune_detr._postprocess(
        out.logits.detach().cpu().numpy(),
        out.pred_boxes.detach().cpu().numpy(),
        band.shape[0], band.shape[1])
    # 換回整幀座標(band 只切了 y)
    boxes = boxes.copy()
    boxes[:, 1] += by0
    boxes[:, 3] += by0
    return boxes, scores, by0


def draw(frame_bgr, gt_boxes, min_score=0.015):
    """回 (標註後的圖, 摘要字串, 每支判到的方向 list)。

    第三個回傳值是給呼叫端做大字顯示用的 —— 不另外再跑一次推論,直接把這裡
    已經算好的結果帶出去(即時預覽每翻一張本來就要跑 0.5~4 秒,不該再乘二)。
    選不出 4 支時回 None。"""
    vis = frame_bgr.copy()
    boxes, scores, _by0 = raw_detections(frame_bgr)
    if boxes is None:
        return vis, "模型不可用", None

    # --- 第 1 層:所有超過門檻的候選(灰) ---
    keep = [i for i in range(len(scores)) if scores[i] >= min_score]
    for i in keep:
        x0, y0, x1, y1 = (int(v) for v in boxes[i])
        cv2.rectangle(vis, (x0, y0), (x1, y1), C_CAND, 1)
        cv2.putText(vis, f"{scores[i]:.3f}", (x0, y0 - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, C_CAND, 1, cv2.LINE_AA)

    # --- 第 2 層:真值框(藍) ---
    for b in gt_boxes or []:
        cv2.rectangle(vis, (int(b["x0"]), int(b["y0"])),
                      (int(b["x1"]), int(b["y1"])), C_GT, 1)

    # --- 第 3 層:幾何選擇挑中的 4 支 + 判向對錯 ---
    sel = rune_detr.detect_arrows(frame_bgr)
    if sel is None:
        return vis, f"候選 {len(keep)} 個,幾何選擇【選不出 4 支】→ 退給 2 線", None

    dirs, err = rune_cv._read_dirs_detr(frame_bgr, sel)
    truth = [b.get("settled_direction") or b.get("label")
             for b in sorted(gt_boxes or [], key=lambda b: b["x0"])]
    n_ok = 0
    for k, (bx, d) in enumerate(zip(sel, dirs)):
        t = truth[k] if k < len(truth) else None
        ok = (d is not None and d == t)
        n_ok += ok
        col = C_SEL_OK if ok else C_SEL_BAD
        x0, y0, x1, y1 = (int(v) for v in bx)
        cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
        cv2.putText(vis, f"{d or '?'}", (x0, y1 + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
        if not ok and t is not None:   # 沒有真值(即時預覽)就不要印 gt:None,那是雜訊
            cv2.putText(vis, f"gt:{t}", (x0, y1 + 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, C_GT, 1, cv2.LINE_AA)
    return (vis, f"候選 {len(keep)} 個 → 選出 4 支,判向對 {n_ok}/4"
            + (f"  ({err})" if err else ""), dirs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--ann", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-score", type=float, default=0.015)
    args = ap.parse_args()

    gt_by_file = {}
    if args.ann and os.path.exists(args.ann):
        data = json.load(open(args.ann, encoding="utf-8"))
        recs = data["records"] if isinstance(data, dict) else data
        gt_by_file = {r["file"]: r.get("boxes", []) for r in recs}

    os.makedirs(args.out_dir, exist_ok=True)
    for p in args.images:
        img = cv2.imread(p)
        if img is None:
            print(f"讀不到 {p}")
            continue
        vis, summary, _dirs = draw(img, gt_by_file.get(os.path.basename(p)), args.min_score)
        bar = np.zeros((30, vis.shape[1], 3), np.uint8)
        cv2.putText(bar, f"{os.path.basename(p)}   {summary}", (8, 21),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        outp = os.path.join(args.out_dir, "viz_" + os.path.basename(p))
        cv2.imwrite(outp, np.vstack([bar, vis]))
        print(f"{os.path.basename(p)}: {summary}")


if __name__ == "__main__":
    main()
