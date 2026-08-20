# -*- coding: utf-8 -*-
"""server/rune_live_state.py 的單元測試 —— 純邏輯(ingest/狀態機)用合成角度序列，
不需要真的遊戲/GPU；擷取邏輯(_capture_burst_angles/observe)用 monkeypatch 頂替
minimap/wgc，只驗證 request_full_rate 的開關時機與還原，不驗證真實影像。

真正的角速度反轉/同步假影/圓形平均邏輯已經在 tests/test_rune_wheel.py 守住，
這裡只驗證「跨呼叫累積 + 逐支狀態機(靜止/旋轉/觀察中)+ 緩衝重置」這一層新邏輯，
且刻意呼叫 rune_wheel 既有函式做判定，不重刻。
"""
import time

import numpy as np
import pytest

import rune_live_state as rls


def _boxes():
    return [(10, 10, 40, 40), (100, 10, 130, 40), (200, 10, 230, 40), (300, 10, 330, 40)]


def _rotating_series(n, start, rate, wobble_frames, target_angle, wobble_len=2, reverse_step=15.0):
    """合成一支持續旋轉、在 wobble_frames 起點各晃動一次的角度序列。做法與
    tests/test_rune_wheel.py::_rotating_with_wobbles 相同（獨立複製一份是因為
    兩個測試檔案目前沒有共用 fixture 模組，各自合成足夠簡單，不值得為此新增
    共用模組）。"""
    wobble_set = set()
    for wf in wobble_frames:
        for k in range(wobble_len):
            wobble_set.add(wf + k)
    angs = []
    a = start
    for i in range(n):
        if i in wobble_frames:
            a = target_angle
        angs.append(a % 360.0)
        a += reverse_step if (i + 1) in wobble_set else rate
    return angs


@pytest.fixture(autouse=True)
def _reset_state():
    rls.reset()
    yield
    rls.reset()


# ---------- ingest：沒偵測到 4 支 → 重置、回全部「觀察中」 ----------
def test_ingest_no_boxes_resets_and_reports_zero():
    status = rls.ingest(None, None, now=100.0)
    assert status["n_boxes_detected"] == 0
    assert status["reset"] is True
    assert status["all_settled"] is False
    assert status["dirs"] is None
    assert len(status["arrows"]) == 4
    assert all(a["motion"] == "unknown" for a in status["arrows"])


# ---------- 靜止：第一次呼叫就能定案(樣本數需 >= MIN_SAMPLES_FOR_CLASSIFY) ----------
def test_ingest_static_arrow_settles_on_first_call():
    boxes = _boxes()
    jitter = [0.3, -0.3] * 5  # 10 個樣本,略高於 MIN_SAMPLES_FOR_CLASSIFY(見稀疏樣本測試)
    angles = [
        [(90.0 + j) % 360.0 for j in jitter],
        [(0.0 + j) % 360.0 for j in jitter],
        [(180.0 + j) % 360.0 for j in jitter],
        [(270.0 + j) % 360.0 for j in jitter],
    ]
    status = rls.ingest(boxes, angles, now=100.0)
    dirs = [a["direction"] for a in status["arrows"]]
    assert dirs == ["up", "right", "left", "down"]
    assert status["all_settled"] is True
    assert status["dirs"] == ["up", "right", "left", "down"]
    assert all(a["motion"] == "static" for a in status["arrows"])


# ---------- 稀疏樣本不該誤判靜止(真實影片踩過的坑,見 .superpowers/live-auto.md) ----------
def test_ingest_does_not_settle_static_from_few_sparse_valid_samples():
    """用 tools/eval_rune_wheel.py 的驗證影片跑 offline_convergence_check.py 時
    實測到的真實失敗案例:影片前 100 幀左右色遮罩幾乎讀不出角度(angle_of 回
    None),直到某一段才剛好累積到 3 個有效樣本、恰好彼此接近 → 圓形集中度 R
    很高 → 被 rune_wheel.STATIC_MIN_SAMPLES(=3)這個門檻誤判成「靜止」,答案
    還是錯的,下一段累積更多樣本後才自我修正回「旋轉」。3 個樣本、其餘幾乎
    全是 None,不該就這樣拍板定案 —— 這裡要求比 rune_wheel 自己的門檻更保守
    的最小有效樣本數才嘗試判斷靜止/旋轉,樣本不足前一律留在「觀察中」。"""
    boxes = _boxes()
    sparse = [None] * 100 + [90.0, 90.2, 89.8]
    status = rls.ingest(boxes, [sparse, sparse, sparse, sparse], now=100.0)
    assert all(a["motion"] == "unknown" for a in status["arrows"]), status["arrows"]
    assert status["all_settled"] is False


# ---------- 跨呼叫累積 ----------
def test_ingest_accumulates_samples_across_calls_with_same_boxes():
    boxes = _boxes()
    s1 = rls.ingest(boxes, [[90.0] * 5 for _ in range(4)], now=100.0)
    assert s1["n_frames"] == 5
    s2 = rls.ingest(boxes, [[90.0] * 3 for _ in range(4)], now=100.3)
    assert s2["n_frames"] == 8
    assert s2["reset"] is False


# ---------- 緩衝重置：超過 STALE_SECS ----------
def test_ingest_resets_when_gap_exceeds_stale_secs():
    boxes = _boxes()
    rls.ingest(boxes, [[90.0] * 5 for _ in range(4)], now=100.0)
    s2 = rls.ingest(boxes, [[90.0] * 2 for _ in range(4)], now=100.0 + rls.STALE_SECS + 0.5)
    assert s2["reset"] is True
    assert s2["n_frames"] == 2


# ---------- 緩衝重置：框位置明顯位移 ----------
def test_ingest_resets_when_boxes_jump():
    boxes_a = _boxes()
    rls.ingest(boxes_a, [[90.0] * 5 for _ in range(4)], now=100.0)
    boxes_b = [(b[0] + 200, b[1], b[2] + 200, b[3]) for b in boxes_a]
    s2 = rls.ingest(boxes_b, [[90.0] * 2 for _ in range(4)], now=100.2)
    assert s2["reset"] is True
    assert s2["n_frames"] == 2


# ---------- 旋轉：要等觀察到晃動才定案，跨呼叫累積後仍能收斂 ----------
def test_ingest_rotating_arrow_settles_after_wobble_observed_across_calls():
    boxes = _boxes()
    n = 90
    rotating = _rotating_series(n, start=0.0, rate=-13.0, wobble_frames=[70], target_angle=270.0)
    static_other = [90.0] * n

    r1, r2 = rotating[:40], rotating[40:]
    s1, s2_ = static_other[:40], static_other[40:]

    status1 = rls.ingest(boxes, [s1, s1, s1, r1], now=100.0)
    assert status1["arrows"][3]["motion"] == "rotating"
    assert status1["arrows"][3]["settled"] is False

    status2 = rls.ingest(boxes, [s2_, s2_, s2_, r2], now=100.5)
    assert status2["arrows"][3]["motion"] == "rotating"
    assert status2["arrows"][3]["settled"] is True
    assert status2["arrows"][3]["direction"] == "down"


# ---------- 混合靜止＋旋轉，逐支獨立判斷(比照 rune_wheel 既有測試) ----------
def test_ingest_handles_mixed_static_and_rotating_like_solve_from_angles():
    boxes = _boxes()
    n = 120
    static_up = [(90.0 + (0.3 if i % 2 == 0 else -0.3)) % 360.0 for i in range(n)]
    static_right = [(2.0 + (0.3 if i % 2 == 0 else -0.3)) % 360.0 for i in range(n)]
    rotating_down = _rotating_series(n, start=15.0, rate=-13.0,
                                      wobble_frames=[20, 50, 80, 108], target_angle=270.0)
    rotating_left = _rotating_series(n, start=200.0, rate=-11.0,
                                      wobble_frames=[15, 45, 75, 105], target_angle=180.0)

    status = rls.ingest(boxes, [static_up, static_right, rotating_down, rotating_left], now=100.0)
    dirs = [a["direction"] for a in status["arrows"]]
    assert dirs == ["up", "right", "down", "left"]
    assert status["all_settled"] is True


# ---------- 擷取:request_full_rate 開關時機與還原 ----------
def test_capture_burst_toggles_full_rate_true_then_false(monkeypatch):
    import minimap
    import wgc

    monkeypatch.setattr(rls, "BURST_SECS", 0.02)
    monkeypatch.setattr(rls, "BURST_POLL_GAP", 0.005)
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    monkeypatch.setattr(minimap, "_grab_window", lambda: frame)
    calls = []
    monkeypatch.setattr(wgc, "request_full_rate", lambda on: calls.append(on))
    monkeypatch.setattr(rls.rune_wheel, "angle_of", lambda crop: 90.0)

    angles = rls._capture_burst_angles(frame, _boxes())

    assert calls[0] is True
    assert calls[-1] is False
    assert calls.count(True) == calls.count(False)
    assert len(angles) == 4
    assert all(len(a) >= 1 for a in angles)


def test_capture_burst_restores_full_rate_even_when_grab_raises(monkeypatch):
    import minimap
    import wgc

    monkeypatch.setattr(rls, "BURST_SECS", 0.05)
    monkeypatch.setattr(rls, "BURST_POLL_GAP", 0.005)
    frame = np.zeros((50, 50, 3), dtype=np.uint8)

    def _boom():
        raise RuntimeError("模擬擷取失敗")
    monkeypatch.setattr(minimap, "_grab_window", _boom)
    calls = []
    monkeypatch.setattr(wgc, "request_full_rate", lambda on: calls.append(on))

    with pytest.raises(RuntimeError):
        rls._capture_burst_angles(frame, _boxes())

    assert calls[0] is True
    assert calls[-1] is False


# ---------- observe()/get_last_status():快取、不重複擷取 ----------
def test_observe_caches_status_for_get_last_status(monkeypatch):
    boxes = _boxes()
    monkeypatch.setattr(rls, "_capture_burst_angles",
                         lambda frame0, boxes4: [[90.0] * 5 for _ in range(4)])
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    status = rls.observe(frame, boxes, now=100.0)
    cached = rls.get_last_status()

    assert cached == status


def test_observe_with_no_boxes_resets_and_records_reason():
    status = rls.observe(None, None, reason="no_frame", now=1.0)
    assert status["n_boxes_detected"] == 0
    assert status["reason"] == "no_frame"


def test_get_last_status_default_before_any_observe():
    status = rls.get_last_status()
    assert status["reason"] == "not_started"


# ==========================================================================
# 常駐背景迴圈(疊圖)
# ==========================================================================
def test_ingest_gap_one_makes_frame_indexes_contiguous():
    """背景迴圈是【連續】取格的,必須能產生連號幀 —— find_wobbles 只在幀號差 1
    時才算角速度,不連號等於每批之間的角速度全部被丟掉(那正是舊的 per-request
    連拍在做的事,見 FRAME_GAP 的註解)。"""
    rls.reset()
    boxes = _boxes()
    for _ in range(3):
        rls.ingest(boxes, [[10.0], [20.0], [30.0], [40.0]], now=1.0, gap=1)
    assert rls._state["frame_idxs"] == [0, 1, 2]


def test_ingest_default_gap_still_marks_missing_frames():
    """不傳 gap 時維持原本語意(呼叫邊界=缺幀),舊呼叫端行為不得改變。"""
    rls.reset()
    boxes = _boxes()
    for _ in range(3):
        rls.ingest(boxes, [[10.0], [20.0], [30.0], [40.0]], now=1.0)
    assert rls._state["frame_idxs"] == [0, rls.FRAME_GAP, rls.FRAME_GAP * 2]


# ---------- 座標邊界:框不能畫到遊戲畫面外面去 ----------
def test_norm_box_clamps_to_frame():
    """模型輸出的框可能超出畫面(箭頭貼在搜尋帶邊緣時)。前端拿它乘上影像矩形
    就會畫到影像外,疊在遠端畫面上看起來像偵測跑到遊戲之外 —— 一律夾回 0~1。"""
    nb = rls._norm_box((-30, -10, 200, 100), 1000, 500)
    assert nb == [0.0, 0.0, 0.2, 0.2]


def test_norm_box_drops_boxes_completely_outside():
    assert rls._norm_box((1200, 10, 1400, 90), 1000, 500) is None
    assert rls._norm_box((-400, 10, -10, 90), 1000, 500) is None


def test_pack_marks_candidates_and_selected():
    """候選框帶信心分數、sel=None;選中的 4 支帶 sel 索引與判定方向。
    兩者【分開帶】而不是從候選裡比對浮點座標找對應(那只會製造對不上的邊界情況)。"""
    cands = [((0, 0, 100, 50), 0.91)]
    boxes4 = [(0, 0, 100, 50), (100, 0, 200, 50), (200, 0, 300, 50), (300, 0, 400, 50)]
    arrows = [{"direction": "up", "motion": "static"}] * 4
    out = rls._pack(cands, boxes4, arrows, 400, 100)
    assert out[0]["score"] == 0.91 and out[0]["sel"] is None
    sel = [o for o in out if o["sel"] is not None]
    assert [o["sel"] for o in sel] == [0, 1, 2, 3]
    assert sel[0]["dir"] == "up"


# ---------- 迴圈生命週期 ----------
class _FakeWgc:
    """記錄 request_full_rate 的進出 —— 不還原的話全速擷取會一直開著,
    吃掉約一顆核心 66%(wgc.py 記著的坑)。"""
    def __init__(self):
        self.balance = 0
        self.n = 0
    def request_full_rate(self, on):
        self.balance += 1 if on else -1
    def latest_ts(self):
        self.n += 1
        return float(self.n)


def _patch_loop(monkeypatch, boxes4=None, frame=None):
    import minimap
    import rune_detr
    import video_pipeline
    import wgc
    fake = _FakeWgc()
    monkeypatch.setattr(wgc, "request_full_rate", fake.request_full_rate)
    monkeypatch.setattr(wgc, "latest_ts", fake.latest_ts)
    frame = np.zeros((200, 400, 3), np.uint8) if frame is None else frame
    monkeypatch.setattr(minimap, "_grab_window", lambda: frame)
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: boxes4)
    monkeypatch.setattr(video_pipeline, "state", dict(video_pipeline.state, source="window"))
    # 候選框那條路會真的載入 RT-DETR 權重跑推論 —— 單元測試不該碰模型/GPU,
    # 這裡直接頂替掉(候選框本身的正確性由 _pack 的測試守)。
    monkeypatch.setattr(rls, "_candidates", lambda viz, frame: [])
    return fake


def test_get_overlay_starts_loop_and_stop_loop_restores_full_rate(monkeypatch):
    fake = _patch_loop(monkeypatch, boxes4=[(0, 0, 20, 20), (30, 0, 50, 20),
                                            (60, 0, 80, 20), (90, 0, 110, 20)])
    rls.reset()
    try:
        snap = rls.get_overlay()
        assert snap["running"] is True
        time.sleep(0.2)
        assert rls.loop_running()
    finally:
        rls.stop_loop()
    assert not rls.loop_running()
    assert fake.balance == 0, "全速擷取的引用計數沒還原"


def test_get_overlay_does_not_start_a_second_loop(monkeypatch):
    _patch_loop(monkeypatch, boxes4=None)
    rls.reset()
    try:
        rls.get_overlay()
        time.sleep(0.05)
        first = rls._loop_thread
        rls.get_overlay()
        assert rls._loop_thread is first
    finally:
        rls.stop_loop()


def test_loop_stops_itself_when_nobody_reads(monkeypatch):
    """忘了關的保險:沒人讀 overlay 超過 LOOP_IDLE_STOP 就自己收掉。"""
    fake = _patch_loop(monkeypatch, boxes4=None)
    monkeypatch.setattr(rls, "LOOP_IDLE_STOP", 0.15)
    rls.reset()
    rls.get_overlay()
    time.sleep(0.6)
    assert not rls.loop_running()
    assert fake.balance == 0
