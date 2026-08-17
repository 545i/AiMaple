# -*- coding: utf-8 -*-
"""跑混合資料集(既有246筆經典款 + 新款297筆)訓練,呼叫既有 train_rune_detr.py
的 main(),不改它任何一行邏輯。

【為什麼需要這個小 runner,不能直接 `python tools/train_rune_detr.py --ann ...`】
train_rune_detr.py 的 load_split() 固定讀 rune_dataset/detr_split.json(檔名/路徑
都寫死在函式裡,不是 CLI 參數),而且用 `[by_file[f] for f in split["train"]]`
——只挑「split 檔案清單裡有列出」的檔名,不是「ann 裡有的全部」。detr_split.json
只列了既有 246/364 張經典款的檔名,不會列到 rune_collect/ 的新檔名。如果直接跑
train_rune_detr.py --ann detr_annotations_mixed.json,新資料的 297 筆會被 split
過濾機制整批默默丟掉,訓練出來的模型其實只吃到旧的 246 筆——這正是規格明文
禁止的「不得改動 train_rune_detr.py 的既有邏輯」與「必須用新舊混合資料訓練」
兩條要求撞在一起的地方。

這裡不改 train_rune_detr.py 一個字,只是:
    1. import 它(規格明講允許 import)
    2. 用 tools/rune_collect_dataset_build.py 已經產生好的
       rune_dataset/detr_split_mixed.json(既有 train/val 檔名原封不動 + 新款
       檔名),取代它的 load_split() 函式參考(monkeypatch 模組屬性,不改檔案)
    3. 組好 sys.argv,呼叫它原本的 main()(訓練迴圈、優化器、checkpoint 存讀、
       resume 邏輯完全不動)

用法(與 train_rune_detr.py 完全相同的參數,直接透傳):
    venv-detr/Scripts/python.exe -X utf8 tools/train_rune_detr_mixed_run.py \
        --img-height 384 --img-width 1344 \
        --ann rune_dataset/detr_annotations_mixed.json \
        --out-dir models/rune_detr_mixed --epochs 150 [--resume] [--max-minutes N]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import train_rune_detr as trd  # noqa: E402

DS_DIR = trd.DS_DIR
SPLIT_MIXED = os.path.join(DS_DIR, "detr_split_mixed.json")


def load_split_mixed(ds_dir=DS_DIR, ann_path=None):
    """與 train_rune_detr.load_split 邏輯完全相同,只是 split 來源換成
    detr_split_mixed.json(既有 train/val 檔名 + rune_collect/ 新款檔名)。"""
    ann_path = ann_path or os.path.join(ds_dir, "detr_annotations.json")
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(SPLIT_MIXED, encoding="utf-8") as f:
        split = json.load(f)
    by_file = {r["file"]: r for r in data["records"]}
    train = [by_file[f] for f in split["train"] if f in by_file]
    val = [by_file[f] for f in split["val"] if f in by_file]
    return train, val


if __name__ == "__main__":
    if not os.path.exists(SPLIT_MIXED):
        print(f"!!! 找不到 {SPLIT_MIXED},先跑 tools/rune_collect_dataset_build.py 產生。")
        sys.exit(1)
    trd.load_split = load_split_mixed
    trd.main()
