# -*- coding: utf-8 -*-
"""拿現行 RT-DETR 箭頭偵測器跑 tools/rune_synth.py v2 產生的「就地編輯真實畫面」
測試集,回答「模型撐不撐得住新款細長旋轉箭頭」。

--------------------------------------------------------------------------
方法論(重要修正,見 .superpowers/rune-synth-v2.md 的除錯記錄)
--------------------------------------------------------------------------
v1 的 eval_synth.py(以及它抄的 tools/eval_rune_detr.py)自己用 transformers
RTDetrForObjectDetection + RTDetrImageProcessor 跑推論,再自己挑「信心最高的
4 個框」配對——這【不是】production 實際在用的框選邏輯。真正的 89.8%/72.8%
基準來自 `server/rune_detr.py::detect_arrows()`:窮舉所有候選框組合,挑「同一
列、間距合理、大小相近、分數夠高」的那組(`SELECT_PARAMS`,見
`.superpowers/detr-select.md`),不是單純取 top-4。這裡直接改用
`rune_cv.evaluate()`同一條路——實測驗證:用「自己重刻 top-4」量真實資料只有
79.0%/54.6%,換成直接呼叫 `rune_detr.detect_arrows()` + `rune_cv._read_dirs_detr()`
(production 原封不動的函式)後,在同一批真實圖上量到 89.3%/72.4%,與檔頭記錄的
89.8%/72.8% 對上(用 rune_cv.evaluate() 直接複驗過)。所以 v2 全部改叫這兩個
函式,不再自己重刻 RT-DETR 推論或框選邏輯——這樣量出來的數字才是「這個系統
撐不撐得住」的真實答案,不是「我重刻的簡化版撐不撐得住」。

副作用:不必再自己 load transformers 模型、不必處理 PIL/torchvision 環境
依賴——`rune_detr.detect_arrows()` 內部用 torch 直接跑,手刻 numpy 前後處理,
與執行期(`server/rune.py`)完全同一份程式碼。

--------------------------------------------------------------------------
座標系
--------------------------------------------------------------------------
rune_synth.py v2 輸出的圖本身就是搜尋帶尺度(1368x347,複製自
rune_dataset/*.png)。`rune_detr.detect_arrows()` 內部會呼叫
`rune_cv.search_band()`,但搜尋帶圖高度 347 < MIN_FRAME_H(400),
search_band() 會直接回 (0, 347)(不裁),所以直接餵這裡的圖沒問題,
輸出座標系與輸入一致。

端到端的真值用每支箭頭的 "label"(目前視覺姿態最接近的基本方向),不是
settled_direction —— 旋轉中的箭頭沒有「正確答案」,色度分割+模板判向本來
就是在讀「畫面上此刻呈現的樣子」,這與 rune_dataset 真實資料的標籤語意一致。

不改動 rune_cv.py / rune_detr.py / 任何既有檔案,只讀取、只呼叫既有函式。
"""
import argparse
import json
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "server"))
import rune_cv  # noqa: E402
import rune_detr  # noqa: E402

DEFAULT_SYNTH_DIR = os.path.join(_ROOT, "rune_synth")

# 對照組:同一個模型在真實資料(rune_dataset/detr_annotations_full.json,364 張)
# 上量到的數字,見 server/rune_detr.py 檔頭表格。用 rune_cv.evaluate()(同一條
# production 路徑)在本機複驗過:365 張正樣本(1464 支箭頭)量到單支 89.3%
# (1308/1464)、四支全對 72.4%(265/366)——與檔頭記錄的 89.8%/72.8% 對得上
# (差距在小數點量級,可能是資料筆數/環境微小差異,不影響結論)。
REAL_BASELINE = {"single": 0.898, "exact": 0.728}


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def eval_one_image(img, gt_boxes, gt_labels):
    """呼叫 production 的 rune_detr.detect_arrows() + rune_cv._read_dirs_detr()
    (兩者皆未改動)。回一張圖的指標。

    位置對應【直接照 slot 順序 k=0..3(依 x 由左到右)】,不做 Hungarian —— 這
    正是遊戲實際的語意:謎題的答案是「第 k 個位置該按哪個方向鍵」,不是「找到
    一個框跟哪個框最像」。detect_arrows() 回傳保證依 x 排序
    (見 server/rune_detr.py::detect_arrows 開頭說明),GT 也是同一個順序建的
    (rune_synth.py 依 k=0..3 由左到右產生)。
    """
    boxes4 = rune_detr.detect_arrows(img)
    exact4 = boxes4 is not None

    slot_matched = [False] * 4
    slot_dir_ok = [False] * 4
    if exact4:
        for k in range(4):
            slot_matched[k] = iou(boxes4[k], gt_boxes[k]) >= 0.5
        dirs, _err = rune_cv._read_dirs_detr(img, list(boxes4))
        for k in range(min(4, len(dirs))):
            slot_dir_ok[k] = dirs[k] == gt_labels[k]

    return {
        "exact4": exact4,
        "slot_matched": slot_matched,
        "slot_dir_ok": slot_dir_ok,
        "all4_ok": exact4 and all(slot_dir_ok),
    }


class Bucket:
    def __init__(self, name):
        self.name = name
        self.n_img = 0
        self.n_exact4 = 0
        self.n_all4_ok = 0
        self.n_gt_arrows = 0
        self.n_matched = 0
        self.n_dir_ok = 0

    def add_image(self, r):
        self.n_img += 1
        self.n_exact4 += int(r["exact4"])
        self.n_all4_ok += int(r["all4_ok"])
        for k in range(4):
            self.n_gt_arrows += 1
            self.n_matched += int(r["slot_matched"][k])
            self.n_dir_ok += int(r["slot_dir_ok"][k])

    def as_dict(self):
        return {
            "name": self.name,
            "n_img": self.n_img,
            "n_arrows": self.n_gt_arrows,
            "recall": self.n_matched / max(1, self.n_gt_arrows),
            "exact4_rate": self.n_exact4 / max(1, self.n_img),
            "e2e_single": self.n_dir_ok / max(1, self.n_gt_arrows),
            "e2e_exact": self.n_all4_ok / max(1, self.n_img),
        }


class SlotBucket:
    """per-arrow(不是 per-image)分層用,is_settled 分層需要這個粒度。"""

    def __init__(self, name):
        self.name = name
        self.n_arrows = 0
        self.n_matched = 0
        self.n_dir_ok = 0

    def add_slot(self, r, k):
        self.n_arrows += 1
        self.n_matched += int(r["slot_matched"][k])
        self.n_dir_ok += int(r["slot_dir_ok"][k])

    def as_dict(self):
        return {
            "name": self.name, "n_arrows": self.n_arrows,
            "recall": self.n_matched / max(1, self.n_arrows),
            "e2e_single": self.n_dir_ok / max(1, self.n_arrows),
        }


def print_bucket(b):
    d = b.as_dict()
    print(f"\n--- {d['name']}  (n_img={d['n_img']}, n_arrows={d['n_arrows']}) ---")
    print(f"  偵測 recall(IoU>=0.5,依 slot 位置對應) {d['recall']:.1%}   "
          f"剛好4支 {d['exact4_rate']:.1%}")
    print(f"  端到端 單支正確率 {d['e2e_single']:.1%}   四支全對 {d['e2e_exact']:.1%}")


def print_slot_bucket(b):
    d = b.as_dict()
    print(f"\n--- {d['name']}  (n_arrows={d['n_arrows']}) ---")
    print(f"  偵測 recall(IoU>=0.5) {d['recall']:.1%}   端到端 單支正確率 {d['e2e_single']:.1%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-dir", default=DEFAULT_SYNTH_DIR)
    ap.add_argument("--ann", default=None,
                    help="標註檔路徑,預設 <synth-dir>/annotations_<stage>.json")
    ap.add_argument("--stage", default=None,
                    help="rune_synth.py v2 每個 stage 各自一個標註檔"
                         "(annotations_a.json..annotations_e.json)。")
    args = ap.parse_args()

    if args.ann:
        ann_path = args.ann
    elif args.stage:
        ann_path = os.path.join(args.synth_dir, f"annotations_{args.stage}.json")
    else:
        ann_path = os.path.join(args.synth_dir, "annotations.json")
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    records = data["records"]
    print(f"合成測試集 {len(records)} 張圖({ann_path})")
    print(f"tolerance_deg={data.get('tolerance_deg')}  stage={data.get('stage')}")

    if not rune_detr.available():
        print("!!! rune_detr 模型不可用(HF_MODEL_DIR 找不到或載入失敗),中止。")
        sys.exit(1)

    overall = Bucket("整體")
    styles_seen = sorted({b["style"] for rec in records for b in rec["boxes"]})
    by_style = {s: Bucket(f"style={s}") for s in styles_seen}
    by_settled = {True: SlotBucket("is_settled=True(已停止)"),
                 False: SlotBucket("is_settled=False(旋轉中)")}

    t0 = time.time()
    for ri, rec in enumerate(records):
        img = rune_cv.imread(os.path.join(args.synth_dir, rec["file"]))
        if img is None:
            continue
        gt_boxes = [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in rec["boxes"]]
        gt_labels = [b["label"] for b in rec["boxes"]]

        r = eval_one_image(img, gt_boxes, gt_labels)
        overall.add_image(r)
        img_style = rec["boxes"][0]["style"] if rec["boxes"] else None
        if img_style in by_style:
            by_style[img_style].add_image(r)
        for k, b in enumerate(rec["boxes"]):
            by_settled[b["is_settled"]].add_slot(r, k)

        if (ri + 1) % 100 == 0:
            print(f"  ...{ri + 1}/{len(records)}", flush=True)

    print(f"\n耗時 {time.time() - t0:.1f}s")
    print("\n" + "=" * 70)
    print("對照組(同一個模型,真實資料 rune_dataset,見 rune_detr.py 檔頭 /"
          " rune_cv.evaluate() 複驗):")
    print(f"  單支 {REAL_BASELINE['single']:.1%}   四支全對 {REAL_BASELINE['exact']:.1%}")
    print("=" * 70)

    print_bucket(overall)
    if len(styles_seen) > 1:
        print("\n===== 分層:style =====")
        for s in styles_seen:
            print_bucket(by_style[s])
    print("\n===== 分層:is_settled(逐支箭頭)=====")
    print_slot_bucket(by_settled[True])
    print_slot_bucket(by_settled[False])


if __name__ == "__main__":
    main()
