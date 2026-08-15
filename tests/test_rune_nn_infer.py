import os

import numpy as np
import pytest

import rune_nn

pytestmark = pytest.mark.skipif(not rune_nn.available(),
                                reason="模型或 onnxruntime 不在")


def test_onnx_matches_torch_reference():
    """ONNX 的輸出必須與訓練當下的 torch 輸出一致(容差 1e-4)。

    這抓的是匯出/前處理不一致的經典 bug:模型照樣跑、照樣回答案,只是答案系統性
    地偏掉,沒有任何錯誤訊息。有這個測試才敢說「上線的跟訓練的是同一個模型」。
    """
    ref = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "rune_arrow_ref.npz"))
    got = rune_nn._session().run(["logits"], {"x": ref["x"]})[0]
    assert np.abs(got - ref["logits"]).max() < 1e-4


def test_predict_returns_per_slot_class_and_prob():
    crops = [np.full((81, 82, 3), 120, np.uint8) for _ in range(4)]
    dirs, probs = rune_nn.predict(crops)
    assert len(dirs) == 4 and len(probs) == 4
    assert all(d in rune_nn.CLASSES for d in dirs)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_predict_reads_real_arrows_correctly():
    """拿資料集裡【現行流程已經讀對】的樣本,模型不能讀錯 —— 那是驗收條件第 2 條
    (不得退步)的最小版本。

    評估對象是全部過閘門樣本,不取前綴 —— index.jsonl 是按時間順序寫入的,取前綴
    (例如前 200 筆)等於按時間切樣本,是有偏樣本而非隨機抽樣。"""
    import bench_arrow_baseline as bench
    import rune_dataset_build as b
    passed, _gated = bench.split_by_current_gate()
    items = [(f, c, d) for f, c, d, _k in b.iter_arrow_crops() if f in passed]
    assert items, "沒有取到過閘門的樣本"
    dirs, _p = rune_nn.predict([c for _f, c, _d in items])
    truth = [d for _f, _c, d in items]
    acc = sum(a == t for a, t in zip(dirs, truth)) / len(truth)
    assert acc >= 0.95, f"在現行流程已讀對的樣本上只有 {acc:.1%}"
