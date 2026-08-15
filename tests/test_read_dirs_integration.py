import numpy as np
import pytest

import rune_cv
import rune_dataset_build as b
import rune_nn


def _first_positive():
    for rec in b.records(b.DS_DIR):
        if not rec.get("negative"):
            img, _box = b.load_sample(b.DS_DIR, rec)
            if img is not None:
                return rec, img
    pytest.skip("資料集裡沒有可用正樣本")


def test_falls_back_when_model_missing(monkeypatch):
    """模型不在時必須完整退回色度分割 —— 沒帶模型的舊 exe 不能壞。

    這是 spec 的硬性約束,所以要有測試,不能只靠讀程式碼相信。
    """
    monkeypatch.setattr(rune_nn, "available", lambda: False)
    rec, img = _first_positive()
    got, err = rune_cv.read_dirs(img, strict=True)
    # 退回舊路徑後,行為必須與這張圖在舊路徑下的結果一致:
    # 要嘛讀出 4 支、要嘛被面積閘門擋下,不能拋例外、不能回奇怪的長度
    assert got == [] or len(got) == 4


@pytest.mark.skipif(not rune_nn.available(), reason="模型不在")
def test_negative_sample_is_rejected():
    """負樣本(畫面上沒謎題)必須什麼都不回報。

    誤判一次的代價是按錯方向鍵、白燒一次符文冷卻,所以這條比「多認出幾支」重要。
    """
    import os
    for rec in b.records(b.DS_DIR):
        if not rec.get("negative"):
            continue
        p = os.path.join(b.DS_DIR, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            continue
        got, _err = rune_cv.read_dirs(img, strict=True)
        assert got == [], f"{rec['file']} 被誤判成 {got}"


@pytest.mark.skipif(not rune_nn.available(), reason="模型不在")
def test_low_confidence_is_rejected(monkeypatch):
    """信心度不足時要退線,不能硬給答案。"""
    monkeypatch.setattr(rune_nn, "predict",
                        lambda crops: (["up"] * 4, [0.10] * 4))
    _rec, img = _first_positive()
    got, err = rune_cv.read_dirs(img, strict=True)
    assert got == []
    assert "信心" in err
