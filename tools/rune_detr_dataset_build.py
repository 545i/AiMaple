"""為 Transformer 物件偵測器(RT-DETR)產生訓練用的箭頭邊界框標註。

背景:`rune_cv.find_capsule()` 定位膠囊只有 54.1% 落在 ±20px 內,是整條符文判向
線路(41%)的瓶頸。判向本身(色度分割 + 模板比對)已經有 97~99% 正確率,不動。
這裡改用預訓練 Transformer 偵測器直接在整張圖上找 4 支箭頭,取代「先定位膠囊、
再等分四格」這個容易連環出錯的兩段式做法。

【框怎麼來】不是假設「箭頭在格中心」——那樣邊界框粗糙,偵測器學不到精確定位。
而是對 box_truth.json 裡人工驗證過的膠囊框,重用 rune_cv._chroma_map / _seg
(既有、已驗證的色度分割,判向 97~99% 正確率就是它撐出來的)取出每格箭頭的實際
遮罩,再取遮罩的 bounding box。這比格子中心點精確得多(實測箭頭約 27x28px,
遠小於 82.25x81 的格子)。取不到遮罩的格子直接跳過那支箭頭(缺標,不是錯標)。

輸出:rune_dataset/detr_annotations.json,一行一張圖:
    {"file", "ts", "width", "height", "boxes": [{"x0","y0","x1","y1","label"}]}
以及 rune_dataset/detr_split.json:{"train": [...files...], "val": [...files...]}
(按 ts 排序後 80/20 切分,同一張圖不跨界 —— 切分本來就是整圖為單位,不會跨界)。

不改動 box_truth.json / index.jsonl / gate_split.json,只讀取。
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import rune_cv  # noqa: E402

DS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rune_dataset")

CAP_W, CAP_H = rune_cv.CAP_W, rune_cv.CAP_H  # 329, 81
DIRS = ["up", "right", "down", "left"]


def load_index(ds_dir=DS_DIR):
    idx = os.path.join(ds_dir, "index.jsonl")
    recs = {}
    with open(idx, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                recs[r["file"]] = r
    return recs


def load_truth(ds_dir=DS_DIR):
    with open(os.path.join(ds_dir, "box_truth.json"), encoding="utf-8") as f:
        data = json.load(f)
    return {r["file"]: r for r in data if "x" in r}


def arrow_boxes_for_capsule(img, x, y):
    """回該張圖膠囊內 4 支箭頭的精確 bbox(全圖座標),取不到的格子回 None。

    完全比照 rune_cv._read_dirs_chroma 的分割邏輯(_chroma_map + _seg),
    只是多一步把分割遮罩轉成 bounding box 而不是拿去判向。
    """
    cap = img[y:y + CAP_H, x:x + CAP_W]
    if cap.shape[0] != CAP_H or cap.shape[1] != CAP_W:
        return [None, None, None, None]
    dist = rune_cv._chroma_map(cap)
    w = cap.shape[1] / 4.0
    out = []
    for k in range(4):
        x0_slot, x1_slot = int(k * w), int((k + 1) * w)
        m = rune_cv._seg(dist, x0_slot, x1_slot)
        if m is None:
            out.append(None)
            continue
        ys, xs = np.where(m > 0)
        if len(xs) == 0:
            out.append(None)
            continue
        # m 的欄索引是相對 [x0_slot:x1_slot] 切片的本地座標,要加回 x0_slot
        # 才是膠囊內座標,再加膠囊左上角 (x, y) 才是全圖座標。
        bx0 = x + x0_slot + int(xs.min())
        bx1 = x + x0_slot + int(xs.max()) + 1
        by0 = y + int(ys.min())
        by1 = y + int(ys.max()) + 1
        out.append((bx0, by0, bx1, by1))
    return out


def build(min_blob=10, ds_dir=DS_DIR):
    index = load_index(ds_dir)
    truth = load_truth(ds_dir)
    records = []
    n_skip_low_conf = 0
    n_skip_no_img = 0
    n_boxes = 0
    n_slots_missing = 0
    for file, t in truth.items():
        if t.get("blob_n", 0) < min_blob:
            n_skip_low_conf += 1
            continue
        rec = index.get(file)
        if rec is None or rec.get("negative"):
            continue
        p = os.path.join(ds_dir, file)
        img = rune_cv.imread(p)
        if img is None:
            n_skip_no_img += 1
            continue
        h, w = img.shape[:2]
        boxes = arrow_boxes_for_capsule(img, t["x"], t["y"])
        dirs = rec["dirs"]
        out_boxes = []
        for k, b in enumerate(boxes):
            if b is None:
                n_slots_missing += 1
                continue
            bx0, by0, bx1, by1 = b
            out_boxes.append({"x0": bx0, "y0": by0, "x1": bx1, "y1": by1,
                              "label": dirs[k]})
            n_boxes += 1
        records.append({
            "file": file,
            "ts": rec["ts"],
            "width": w,
            "height": h,
            "blob_n": t.get("blob_n", 0),
            "boxes": out_boxes,
        })
    records.sort(key=lambda r: r["ts"])

    n = len(records)
    n_train = int(round(n * 0.8))
    train = records[:n_train]
    val = records[n_train:]

    print(f"truth 可信(blob_n>={min_blob}): {n} 張圖 "
          f"(門檻不足跳過 {n_skip_low_conf}, 讀圖失敗跳過 {n_skip_no_img})")
    print(f"箭頭框: {n_boxes} 個(遺失格子 {n_slots_missing}, "
          f"平均每圖 {n_boxes / max(1, n):.2f} 支)")
    print(f"切分: train {len(train)} 張 / val {len(val)} 張 "
          f"(按 ts 排序後 80/20,同張圖不跨界)")

    return records, train, val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-blob", type=int, default=10)
    ap.add_argument("--ds-dir", default=DS_DIR)
    ap.add_argument("--out", default=os.path.join(DS_DIR, "detr_annotations.json"))
    args = ap.parse_args()

    records, train, val = build(args.min_blob, args.ds_dir)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"min_blob": args.min_blob, "records": records}, f,
                  ensure_ascii=False, indent=1)
    split_path = os.path.join(os.path.dirname(args.out), "detr_split.json")
    with open(split_path, "w", encoding="utf-8") as f:
        json.dump({"train": [r["file"] for r in train],
                   "val": [r["file"] for r in val]}, f, ensure_ascii=False, indent=1)
    print(f"寫入 {args.out}")
    print(f"寫入 {split_path}")


if __name__ == "__main__":
    main()
