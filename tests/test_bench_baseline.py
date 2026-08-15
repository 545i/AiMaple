import bench_arrow_baseline as bench


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
