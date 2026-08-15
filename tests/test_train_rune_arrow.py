import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="訓練相關測試只在 venv-train 裡跑")

import train_rune_arrow as t


def test_split_by_time_has_no_leak():
    """按 ts 排序切分,不隨機切。

    同一顆符文、同一張地圖、相近時間的樣本背景高度相關,隨機切會讓相關樣本
    跨越訓練/驗證邊界,把分數灌到虛高 —— 那種分數上線後會原形畢露。
    """
    items = [{"ts": i, "v": i} for i in range(100)]
    rng = np.random.default_rng(0)
    rng.shuffle(items)
    train, val = t.split_by_time(items)
    assert len(train) == 80 and len(val) == 20
    assert max(x["ts"] for x in train) < min(x["ts"] for x in val)


def test_augment_preserves_shape_and_changes_pixels():
    rng = np.random.default_rng(0)
    crop = np.random.default_rng(1).integers(
        0, 256, (81, 82, 3), dtype=np.uint8)
    out = t.augment(crop, rng)
    assert out.shape == crop.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(out, crop), "增強完全沒動到像素"


def test_model_can_overfit_a_tiny_batch():
    """32 筆訓 300 輪必須背起來(>95%)。

    這是標準的 ML sanity check:它不證明模型會泛化,但能抓出「模型與訓練迴圈根本
    沒接起來」這類靜默失敗(標籤錯位、loss 沒回傳、學習率壞掉),而那類錯誤靠看
    最終分數是分不出來的 —— 泛化不好與根本沒在學,分數長得一樣。
    """
    g = torch.Generator().manual_seed(0)
    x = torch.rand(32, 3, 32, 32, generator=g)
    y = torch.randint(0, 5, (32,), generator=g)
    model = t.ArrowNet()
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    lossf = torch.nn.CrossEntropyLoss()
    for _ in range(300):
        opt.zero_grad()
        loss = lossf(model(x), y)
        loss.backward()
        opt.step()
    model.eval()
    with torch.no_grad():
        acc = (model(x).argmax(1) == y).float().mean().item()
    assert acc > 0.95, f"連 32 筆都背不起來(acc={acc:.2f}),訓練迴圈有問題"
