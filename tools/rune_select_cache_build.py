"""對 squash / aspect 兩個 RT-DETR 模型,在 blob_n>=0 的 364 張圖上各跑一次推論,
把 boxes/scores/labels + GT 存成 json 快取,供 tune_rune_select.py /
eval_rune_select.py 重複使用(避免每次調參都要重新跑 GPU 推論)。

只讀取既有模型與資料集,不寫回 rune_dataset/、不動 models/,快取檔存在
scratchpad,不進 git。

重用 tools/eval_rune_detr.py 的 load_model / infer(不改動該檔案)。
"""
import argparse
import json
import os
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

from eval_rune_detr import load_model, infer  # noqa: E402
import rune_cv  # noqa: E402

DS_DIR = os.path.join(ROOT, "rune_dataset")


def build_cache(model_dir, ann_path, out_path, device):
    model, processor = load_model(model_dir, device)
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    recs = data["records"]
    results = []
    t_infer = []
    for ri, rec in enumerate(recs):
        bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
        if bgr is None:
            print(f"!! 讀不到 {rec['file']},跳過")
            continue
        boxes, scores, labels, dt = infer(model, processor, bgr, device, 0.0)
        if ri >= 3:
            t_infer.append(dt)
        results.append({
            "file": rec["file"],
            "blob_n": rec.get("blob_n"),
            "gt_boxes": [[b["x0"], b["y0"], b["x1"], b["y1"]] for b in rec["boxes"]],
            "gt_labels": [b["label"] for b in rec["boxes"]],
            "boxes": boxes.tolist(),
            "scores": scores.tolist(),
            "labels": labels.tolist(),
        })
        if (ri + 1) % 50 == 0:
            print(f"  {ri + 1}/{len(recs)}", flush=True)
    payload = {
        "model_dir": model_dir,
        "ann_path": ann_path,
        "n": len(results),
        "mean_infer_ms": (sum(t_infer) / len(t_infer) * 1000) if t_infer else None,
        "records": results,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"寫入 {out_path}  n={len(results)}  平均推論 {payload['mean_infer_ms']}ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,中止。")
        sys.exit(1)

    ann_full = os.path.join(DS_DIR, "detr_annotations_full.json")  # blob_n>=0, 364 張

    models = {
        "squash": os.path.join(ROOT, "models", "rune_detr", "final"),
        "aspect": os.path.join(ROOT, "models", "rune_detr_ar", "final"),
    }
    for name, model_dir in models.items():
        print(f"===== {name}: {model_dir} =====")
        t0 = time.time()
        out_path = os.path.join(args.out_dir, f"det_cache_{name}_full364.json")
        build_cache(model_dir, ann_full, out_path, device)
        print(f"  耗時 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
