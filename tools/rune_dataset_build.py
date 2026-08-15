"""從 rune_dataset 萃取每格箭頭小圖與標籤,供訓練使用。

【為什麼可以直接信任這批 crop】膠囊框實測零誤差(被閘門擋下的 199 筆,框全都是
329x81,p5=p50=p95),所以四等分切出來的格子與標籤是對得上的。訓練資料最容易出錯
的地方就在這裡,而這個專案剛好沒有這個問題。

標籤來自 index.jsonl 的 dirs,那是【遊戲驗證過】的(按完四個方向後紫標消失)。
不要改成用任何模型的輸出當標籤。
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import rune_cv  # noqa: E402

# 與 rune_nn.CLASSES 同序。前四項順時針,所以 np.rot90(逆時針 k 次)等於索引往回退 k。
DIRS = ["up", "right", "down", "left"]


def rot_label(label, k):
    """影像經 np.rot90(img, k) 之後的新標籤。

    np.rot90 是【逆時針】,而 DIRS 是順時針排列,所以索引往回退 k。
    這個方向不要用推的 —— 見 test_rot_label_matches_project_direction_reader,
    那個測試拿 rune_cv 自己的判向器當裁判驗過。
    """
    if label == "none":
        return "none"
    return DIRS[(DIRS.index(label) - k) % 4]


DS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rune_dataset")


def records(ds_dir=DS_DIR):
    """逐筆讀 index.jsonl。公開介面 —— 後面三個腳本都要用。"""
    idx = os.path.join(ds_dir, "index.jsonl")
    with open(idx, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_sample(ds_dir, rec):
    """回 (影像, 膠囊框);任一取不到回 (None, None)。

    capsule 欄位是存樣本當下 find_capsule 的結果,舊樣本可能沒有 → 現場重算。
    """
    p = os.path.join(ds_dir, rec["file"])
    if not os.path.exists(p):
        return None, None
    img = rune_cv.imread(p)
    if img is None:
        return None, None
    box = rec.get("capsule") or rune_cv.find_capsule(img)
    return (img, box) if box else (None, None)


def iter_arrow_crops(ds_dir=DS_DIR):
    """產生 (檔名, crop_bgr, 標籤, 格索引)。找不到膠囊框的樣本直接跳過。"""
    for rec in records(ds_dir):
        if rec.get("negative"):
            continue
        img, box = load_sample(ds_dir, rec)
        if img is None:
            continue
        for k, (x0, y0, x1, y1) in enumerate(rune_cv.slots(box, 4)):
            crop = img[y0:y1, x0:x1]
            if crop.size:
                yield rec["file"], crop, rec["dirs"][k], k


def iter_negative_crops(ds_dir=DS_DIR, seed=0):
    """產生 (檔名, crop_bgr, 取樣框):與膠囊框【完全不重疊】的同尺寸區塊,每張圖 1 個。

    每張圖只取 1 個是刻意的:1408 支箭頭經 rot90 增強後每個方向 1408 筆,
    352 個負樣本同樣增強後也是 1408 筆,五類剛好平衡。

    取樣框要一起回傳,否則測試無法驗證「真的沒疊到膠囊」—— 疊到就是錯標,
    而錯標是靜默的,只會表現為分數不如預期。
    """
    rng = np.random.default_rng(seed)
    for rec in records(ds_dir):
        if rec.get("negative"):
            continue
        img, box = load_sample(ds_dir, rec)
        if img is None:
            continue
        h, w = img.shape[:2]
        bw = (box[2] - box[0]) // 4
        bh = box[3] - box[1]
        if w - bw <= 0 or h - bh <= 0:
            continue
        for _ in range(50):
            x = int(rng.integers(0, w - bw + 1))
            y = int(rng.integers(0, h - bh + 1))
            overlap = not (x + bw <= box[0] or x >= box[2] or
                           y + bh <= box[1] or y >= box[3])
            if overlap:
                continue
            crop = img[y:y + bh, x:x + bw]
            if crop.size:
                yield rec["file"], crop, (x, y, x + bw, y + bh)
            break
