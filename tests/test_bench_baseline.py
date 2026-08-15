import glob
import os

import pytest

import bench_arrow_baseline as bench
import rune_dataset_build as b

# rune_dataset/*.png 被 gitignore。新 clone 或還沒掛過機的機器上這裡是空的,
# iter_arrow_crops() 會產出 0 筆,test_baseline_returns_a_direction 的
# next(iter(...)) 會直接 StopIteration —— 那是「沒有資料」不是程式碼壞了。
if not glob.glob(os.path.join(b.DS_DIR, "*.png")):
    pytest.skip(
        "rune_dataset/ 沒有任何 .png(被 gitignore,新 clone 或全新機器上是空的)"
        " —— 這些測試需要實際樣本資料,不是程式碼本身的問題",
        allow_module_level=True,
    )


def test_gate_split_matches_measured_numbers():
    """實作前量過:153 筆過閘門、199 筆被擋下(另 13 筆找不到框)。

    這個切分是驗收條件第 2 條的分母,對不上就無從判斷「有沒有退步」。
    """
    passed, gated = bench.split_by_current_gate()
    assert len(passed) == 153, f"過閘門 {len(passed)} 筆,期望 153"
    assert len(gated) == 199, f"被擋下 {len(gated)} 筆,期望 199"


def test_baseline_returns_a_direction():
    import rune_dataset_build as b
    _f, crop, _d, _k = next(iter(b.iter_arrow_crops()))
    assert bench.baseline_dir(crop) in {"up", "right", "down", "left"}
