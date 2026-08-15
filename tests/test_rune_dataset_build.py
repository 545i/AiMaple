import glob
import os

import numpy as np
import pytest

import rune_dataset_build as b

# rune_dataset/*.png 被 gitignore(見 rune-dataset.md)。新 clone 或還沒掛過機的
# 機器上這裡是空的,iter_arrow_crops() 會產出 0 筆 —— 那是「沒有資料」不是
# 「萃取邏輯壞了」,不該讓整個測試檔炸成一片紅。
if not glob.glob(os.path.join(b.DS_DIR, "*.png")):
    pytest.skip(
        "rune_dataset/ 沒有任何 .png(被 gitignore,新 clone 或全新機器上是空的)"
        " —— 這些測試需要實際樣本資料,不是程式碼本身的問題",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def crops():
    return list(b.iter_arrow_crops())


def test_crop_count_matches_dataset(crops):
    """352 筆找得到膠囊框的正樣本 × 4 格 = 1408 支箭頭,這是實作前量過的基準。

    用「不得少於」而不是「等於」:資料集會隨掛機持續累積(server/rune.py 的
    DATASET_MAX=2000,解一次符文就往 index.jsonl append 一筆),筆數只會漲不會跌。
    低於基準才代表萃取漏了樣本或多切了格子;高於基準是正常成長,不是錯誤訊號。
    """
    assert len(crops) >= 1408


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


def test_rot_label_matches_project_direction_reader():
    """rot90 的標籤變換必須與專案既有判向器一致。

    不用人腦推導旋轉方向 —— 拿 rune_cv 自己的模板與判向器當裁判。
    這是整個增強策略的地基:錯了就等於把 1408 支箭頭全部錯標成四倍。
    """
    import numpy as np
    import rune_cv
    assert rune_cv._TPL is not None, "判向模板沒載到,無法驗證"
    up_mask = (rune_cv._TPL > 0.5).astype(np.uint8) * 255
    for k in range(4):
        rotated = np.ascontiguousarray(np.rot90(up_mask, k))
        assert rune_cv._direction_tpl(rotated) == b.rot_label("up", k), \
            f"k={k} 的標籤變換與判向器不一致"


def test_rot_label_leaves_none_alone():
    """負樣本轉了還是負樣本。"""
    for k in range(4):
        assert b.rot_label("none", k) == "none"
