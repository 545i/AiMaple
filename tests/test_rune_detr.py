import numpy as np
import pytest

import rune_cv
import rune_dataset_build as b
import rune_detr


def _first_positive():
    for rec in b.records(b.DS_DIR):
        if not rec.get("negative"):
            img, _box = b.load_sample(b.DS_DIR, rec)
            if img is not None:
                return rec, img
    pytest.skip("資料集裡沒有可用正樣本")


def test_falls_back_to_existing_pipeline_when_model_unavailable(monkeypatch):
    """rune_detr 不可用時,read_dirs 必須逐位元退回現行 find_capsule +
    _read_dirs_chroma 流程 —— 這是任務的硬性約束(spec 明講「模型不可用時,
    read_dirs 的行為必須與現在【逐位元一致】」),不能只靠讀程式碼相信。"""
    monkeypatch.setattr(rune_detr, "available", lambda: False)
    rec, img = _first_positive()

    got, err = rune_cv.read_dirs(img, strict=True)

    box = rune_cv.find_capsule(img)
    if box is None:
        want = ([], "找不到謎題膠囊(還沒開謎題?)")
    else:
        want = rune_cv._read_dirs_chroma(img, box, True)
    assert (got, err) == want


def test_strict_false_semantics_unchanged_when_model_unavailable(monkeypatch):
    """strict=False(預覽診斷用)在舊路徑上的語意不能被這次改動動到:直接呼叫
    _read_dirs_chroma(strict=False) 與透過 read_dirs(strict=False) 拿到的結果
    必須一致。"""
    monkeypatch.setattr(rune_detr, "available", lambda: False)
    rec, img = _first_positive()

    got, err = rune_cv.read_dirs(img, strict=False)

    box = rune_cv.find_capsule(img)
    if box is None:
        want = ([], "找不到謎題膠囊(還沒開謎題?)")
    else:
        want = rune_cv._read_dirs_chroma(img, box, False)
    assert (got, err) == want


def test_read_dirs_uses_detr_path_when_available(monkeypatch):
    """rune_detr 可用且選得出 4 支箭頭時,read_dirs 必須真的走新路徑(不能靜默
    落回舊路徑)—— 用真值框直接餵 detect_arrows,確認 read_dirs 回傳的方向與
    真值一致,且 rune_nn/find_capsule 都沒被呼叫到。"""
    rec, img = _first_positive()
    box = rune_cv.find_capsule(img)
    if box is None:
        pytest.skip("這張正樣本本身 find_capsule 就找不到,不適合這個測試")
    slots4 = rune_cv.slots(box, 4)

    monkeypatch.setattr(rune_detr, "available", lambda: True)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda frame: slots4)

    def _boom(*a, **kw):
        raise AssertionError("不應該落回 find_capsule 路徑")
    monkeypatch.setattr(rune_cv, "find_capsule", _boom)

    got, _err = rune_cv.read_dirs(img, strict=True)
    assert len(got) == 4


def test_select_arrows_returns_none_when_candidates_insufficient():
    """候選不足 4 個必須回 None(失敗,交給上層退回舊路徑),不能湊數或外插。"""
    boxes = [(0, 0, 10, 10), (20, 0, 30, 10), (40, 0, 50, 10)]  # 只有 3 個
    scores = [0.9, 0.9, 0.9]
    assert rune_detr.select_arrows(boxes, scores) is None


def test_select_arrows_returns_none_when_all_below_min_score():
    """4 個候選但全部低於 min_score 門檻,篩完剩不到 4 個,一樣要回 None。"""
    boxes = [(0, 0, 10, 10), (20, 0, 30, 10), (40, 0, 50, 10), (60, 0, 70, 10)]
    scores = [0.001, 0.001, 0.001, 0.001]
    assert rune_detr.select_arrows(boxes, scores) is None


def test_select_arrows_picks_the_geometrically_coherent_group():
    """候選裡混了一批排成一列、間距接近 gap_expected 的「真箭頭」,與一批亂放的
    高分雜訊框(y 不對齊、大小不一)。幾何選擇要挑出前者,不能被分數牽著走。"""
    gap = rune_detr.SELECT_PARAMS["gap_expected"]
    y0, y1 = 40, 68  # 高 28px,寬 27px 左右,貼近實測統計量
    real = [(int(k * gap), y0, int(k * gap) + 27, y1) for k in range(4)]
    real_scores = [0.05, 0.05, 0.05, 0.05]
    # 雜訊:分數更高,但 y 亂跳、大小也不一致
    noise = [(300, 5, 340, 60), (500, 200, 560, 260),
             (700, 10, 705, 15), (900, 100, 990, 240)]
    noise_scores = [0.3, 0.3, 0.3, 0.3]

    boxes = real + noise
    scores = real_scores + noise_scores
    sel = rune_detr.select_arrows(boxes, scores)
    assert sel is not None
    assert len(sel) == 4
    got = sorted(tuple(int(v) for v in bx) for bx in sel)
    want = sorted(real)
    assert got == want


def test_select_arrows_rejects_overlapping_combo():
    """4 個框裡有兩個幾乎完全重疊(同一支箭頭的重複偵測)—— 若候選正好只有這 4
    個,沒有別的組合可選,必須回 None,不能把重複偵測當成兩支不同的箭頭。"""
    boxes = [(0, 0, 10, 10), (1, 1, 11, 11), (84, 0, 94, 10), (168, 0, 178, 10)]
    scores = [0.9, 0.9, 0.9, 0.9]
    assert rune_detr.select_arrows(boxes, scores) is None


def test_detect_arrows_returns_none_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(rune_detr, "_session", lambda: None)
    _rec, img = _first_positive()
    assert rune_detr.detect_arrows(img) is None
