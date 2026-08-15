import numpy as np
import pytest

import rune_dataset_build as b


@pytest.fixture(scope="module")
def crops():
    return list(b.iter_arrow_crops())


def test_crop_count_matches_dataset(crops):
    """352 筆找得到膠囊框的正樣本 × 4 格 = 1408 支箭頭。

    這個數字是實作前量過的基準;對不上表示萃取漏了樣本或多切了格子。
    """
    assert len(crops) == 1408


def test_every_crop_is_one_slot_sized(crops):
    """膠囊框實測恆為 329x81,四等分後每格約 82x81。

    尺寸跑掉代表框抓錯或等分算錯 —— 那會讓圖與標籤對不上,是最該擋下的錯誤。
    """
    for _f, img, _d, _k in crops:
        h, w = img.shape[:2]
        assert 70 <= w <= 95, f"格寬 {w} 不合理"
        assert 70 <= h <= 95, f"格高 {h} 不合理"


def test_labels_are_valid_directions(crops):
    assert {d for _f, _i, d, _k in crops} == {"up", "right", "down", "left"}


def test_labels_roughly_balanced(crops):
    """四個方向在資料集裡本來就接近均勻(393/372/350/345)。
    嚴重偏斜代表萃取時格索引與標籤對錯位。"""
    from collections import Counter
    c = Counter(d for _f, _i, d, _k in crops)
    assert max(c.values()) / min(c.values()) < 1.3, c


def test_negative_crops_do_not_overlap_capsule():
    """負樣本必須完全在膠囊框外 —— 疊到箭頭就等於把正樣本標成 none,是錯標。

    這裡不只數數量,而是【重新驗證每一個負樣本與膠囊框沒有交集】:重疊判斷寫錯
    是靜默的,錯標會安靜地混進訓練集,只表現為分數不如預期。
    """
    boxes = {}
    for rec in b.records():
        if rec.get("negative"):
            continue
        img, box = b.load_sample(b.DS_DIR, rec)
        if img is not None:
            boxes[rec["file"]] = box
    n = 0
    for fname, crop, (x0, y0, x1, y1) in b.iter_negative_crops():
        n += 1
        assert crop.size > 0
        bx = boxes[fname]
        disjoint = (x1 <= bx[0] or x0 >= bx[2] or y1 <= bx[1] or y0 >= bx[3])
        assert disjoint, f"{fname} 的負樣本 {(x0, y0, x1, y1)} 疊到膠囊 {bx}"
        assert crop.shape[1] == (bx[2] - bx[0]) // 4, "負樣本寬度要等於一格箭頭"
    assert n > 300, f"負樣本只生出 {n} 個,不足以平衡五個類別"
