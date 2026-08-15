import numpy as np

import rune_nn


def test_shape_dtype_and_range():
    crop = np.full((81, 82, 3), 128, np.uint8)
    x = rune_nn.preprocess(crop)
    assert x.shape == (3, 32, 32)
    assert x.dtype == np.float32
    assert 0.0 <= x.min() and x.max() <= 1.0


def test_channel_order_is_rgb():
    """輸入是 OpenCV 的 BGR,輸出必須是 RGB。

    這條錯了模型還是會訓得起來、也不會報錯,只是訓練與推論各吃各的順序,
    分數莫名其妙掉一截 —— 正是最難查的那種錯,所以要有測試釘住。
    """
    blue_in_bgr = np.zeros((81, 82, 3), np.uint8)
    blue_in_bgr[:, :, 0] = 255            # BGR 的 B 通道
    x = rune_nn.preprocess(blue_in_bgr)
    assert x[0].max() == 0.0, "R 通道不該有值"
    assert x[2].min() == 1.0, "B 通道應該全滿"


def test_batch_shape():
    crops = [np.full((81, 82, 3), 100, np.uint8) for _ in range(4)]
    x = rune_nn.preprocess_batch(crops)
    assert x.shape == (4, 3, 32, 32)
    assert x.dtype == np.float32


def test_classes_order_matches_rune_cv():
    """前四項必須與 rune_cv._TPL_DIRS 同序 —— rot90 增強依賴這個順序。"""
    import rune_cv
    assert rune_nn.CLASSES[:4] == rune_cv._TPL_DIRS
    assert rune_nn.CLASSES[4] == "none"
