import os

import numpy as np
import pytest

import rune_nn

pytestmark = pytest.mark.skipif(not rune_nn.available(),
                                reason="模型或 onnxruntime 不在")


def test_onnx_matches_torch_reference():
    """兩條各自獨立的防線,合起來才敢說「上線的跟訓練的是同一個模型」:

    1) 前處理漂移:rune_nn.preprocess_batch 對【訓練當下存下的原始 crop】重新算出
       的 x,必須與訓練當下存的 x 一致(容差 1e-6)。抓的是 train/serve 前處理分岔
       這種經典 bug —— 模型照樣跑、照樣回答案,只是答案系統性地偏掉,沒有任何錯誤
       訊息。
    2) 匯出漂移:ONNX session 對(第 1 條重新算出的)x 的輸出,必須與訓練當下的
       torch 輸出一致(容差 1e-4)。抓的是匯出過程本身(算子精度、權重沒對齊等)
       出的問題。

    這兩條缺一不可:早先版本只做了第 2 條、卻直接餵 npz 裡存好的 x(從未呼叫
    preprocess),docstring 卻宣稱涵蓋前處理 —— 把 preprocess 的 /255.0 拿掉,那個
    版本照樣 PASSED。假承諾比沒有測試更糟,因為它讓人以為那條防線存在。
    """
    ref = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "rune_arrow_ref.npz"))
    n = ref["x"].shape[0]
    crops = [ref[f"crop_{i}"] for i in range(n)]

    x = rune_nn.preprocess_batch(crops)
    diff_pre = float(np.abs(x - ref["x"]).max())
    assert diff_pre < 1e-6, (
        f"前處理漂移:rune_nn.preprocess_batch 對訓練當下存下的原始 crop 重新算出的 "
        f"x,與訓練當下存的 x 差了 {diff_pre} —— 代表現在的 preprocess 跟訓練時用的"
        f"不是同一份邏輯")

    got = rune_nn._session().run(["logits"], {"x": x})[0]
    diff_export = float(np.abs(got - ref["logits"]).max())
    assert diff_export < 1e-4, (
        f"匯出漂移:ONNX 對(重新算出的)x 的輸出,與訓練當下的 torch 輸出差了 "
        f"{diff_export} —— 代表 ONNX 匯出過程本身出了問題,不是前處理造成的(上面那條"
        f"已經先過了)")


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
