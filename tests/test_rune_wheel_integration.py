# -*- coding: utf-8 -*-
"""server/rune.py 旋轉分支的接線測試(不需要真的遊戲/GPU,全部用 monkeypatch)。

重點守兩件事:
  1. 沒有旋轉訊號時,_cv_read / _detect 的行為要跟改動前【逐位元一致】——
     這是任務的硬性約束(「現有的靜態路徑行為不得改變」),不能只靠讀程式碼
     相信,要真的跑一次斷言。
  2. 有旋轉訊號時,新分支要真的接得上:_rotating_signal 判斷正確、
     _solve_wheel 正確裁切座標系統並還原 wgc 全速擷取的引用計數、
     _detect 正確把結果標成 line="wheel"。

真正的判向正確性(角速度反轉/同步假影/圓形平均/混合靜止旋轉)已經在
tests/test_rune_wheel.py 用合成角度序列守住,這裡不重複測那些。
"""
import numpy as np
import pytest

import rune


@pytest.fixture(autouse=True)
def _restore_globals(monkeypatch):
    """這幾個是模組層級的可變狀態(辨識線路開關、WHEEL_ENABLED),測試改了要還原,
    不然會互相汙染、也會汙染同一個 session 裡跑在後面的其他測試檔。"""
    monkeypatch.setattr(rune, "_line_cv", True)
    monkeypatch.setattr(rune, "_line_claude", True)
    monkeypatch.setattr(rune, "WHEEL_ENABLED", True)
    yield


def _dummy_frame():
    return np.zeros((10, 10, 3), dtype=np.uint8)


# ---------- _cv_read:沒有旋轉訊號時的行為不能變 ----------
def test_cv_read_early_agreement_never_calls_rotating_signal(monkeypatch):
    """兩幀就讀到一致答案時,_cv_read 在迴圈內直接 return —— 這是改動前就有的
    行為,不該被新加的旋轉判斷影響。用「呼叫 _rotating_signal 就丟例外」確保
    這條路徑真的完全沒碰到新程式碼。"""
    import minimap
    import rune_cv

    monkeypatch.setattr(rune_cv, "read_dirs", lambda frame: (["up", "down", "left", "right"], ""))
    monkeypatch.setattr(minimap, "_grab_window", lambda: _dummy_frame())

    def _boom(_frames):
        raise AssertionError("不應該呼叫 _rotating_signal —— 已經提早一致了")
    monkeypatch.setattr(rune, "_rotating_signal", _boom)

    dirs, err, tried, hit_frame, rotating, wheel_boxes = rune._cv_read(_dummy_frame())
    assert dirs == ["up", "down", "left", "right"]
    assert err == ""
    assert tried == 2
    assert rotating is False
    assert wheel_boxes is None


def test_cv_read_disagreement_without_rotating_signal_matches_old_message(monkeypatch):
    """CV 幾幀不一致、且不是旋轉款(_rotating_signal 判 False)—— 錯誤訊息與
    回傳結構要跟舊版邏輯完全一致(只是多了 rotating=False, wheel_boxes=None
    兩個新欄位),這是「靜態路徑行為不變」的直接證據。"""
    import minimap
    import rune_cv

    # 5 組兩兩相異的答案,確保 CV_TRIES=5 次裡不會有任何一組重複出現兩次
    # (重複兩次就會提早湊到 CV_AGREE 而 return,不會走到「不一致」這條路)。
    cycle = [
        ["up", "down", "left", "right"],
        ["down", "up", "right", "left"],
        ["left", "right", "up", "down"],
        ["right", "left", "down", "up"],
        ["up", "left", "down", "right"],
    ]
    calls = {"n": 0}

    def _read_dirs(frame):
        d = cycle[calls["n"] % len(cycle)]
        calls["n"] += 1
        return d, ""

    monkeypatch.setattr(rune_cv, "read_dirs", _read_dirs)
    monkeypatch.setattr(minimap, "_grab_window", lambda: _dummy_frame())
    monkeypatch.setattr(rune, "_rotating_signal", lambda frames: (False, None))

    dirs, err, tried, hit_frame, rotating, wheel_boxes = rune._cv_read(_dummy_frame())
    assert dirs == []
    assert tried == rune.CV_TRIES
    assert hit_frame is None
    assert rotating is False
    assert wheel_boxes is None
    assert "未取得一致答案" in err


# ---------- _rotating_signal ----------
def test_rotating_signal_true_when_stable_4box_every_frame(monkeypatch):
    import rune_detr
    stable_boxes = [(10, 10, 40, 40), (100, 12, 130, 42),
                    (200, 11, 230, 41), (300, 10, 330, 40)]
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: stable_boxes)

    rotating, boxes4 = rune._rotating_signal([_dummy_frame()] * rune.CV_TRIES)
    assert rotating is True
    assert len(boxes4) == 4
    # 依 x 由左到右排序,且數值上等於(平均後仍相同)原始框
    xs = [b[0] for b in boxes4]
    assert xs == sorted(xs)


def test_rotating_signal_false_when_boxes_jitter_too_much(monkeypatch):
    import rune_detr
    frames = [_dummy_frame(), _dummy_frame()]
    boxes_seq = [
        [(10, 10, 40, 40), (100, 12, 130, 42), (200, 11, 230, 41), (300, 10, 330, 40)],
        # 第二幀第一支箭頭大幅位移(遠超 ROTATE_BOX_JITTER)—— 不該判定為旋轉
        [(10 + 5 * rune.ROTATE_BOX_JITTER, 10, 40 + 5 * rune.ROTATE_BOX_JITTER, 40),
         (100, 12, 130, 42), (200, 11, 230, 41), (300, 10, 330, 40)],
    ]
    it = iter(boxes_seq)
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: next(it))

    rotating, boxes4 = rune._rotating_signal(frames)
    assert rotating is False
    assert boxes4 is None


def test_rotating_signal_false_when_any_frame_misses_4_boxes(monkeypatch):
    import rune_detr
    frames = [_dummy_frame(), _dummy_frame()]
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: None)

    rotating, boxes4 = rune._rotating_signal(frames)
    assert rotating is False
    assert boxes4 is None


def test_rotating_signal_false_when_detr_unavailable(monkeypatch):
    import rune_detr
    monkeypatch.setattr(rune_detr, "available", lambda: False)
    rotating, boxes4 = rune._rotating_signal([_dummy_frame()])
    assert rotating is False
    assert boxes4 is None


# ---------- _solve_wheel:裁切座標平移 + wgc 全速擷取的引用計數還原 ----------
def test_solve_wheel_shifts_boxes_and_restores_full_rate(monkeypatch):
    import minimap
    import rune_wheel
    import wgc

    monkeypatch.setattr(rune, "WHEEL_CAPTURE_SECS", 0.03)
    monkeypatch.setattr(rune, "WHEEL_POLL_GAP", 0.005)
    monkeypatch.setattr(rune, "WHEEL_MIN_FRAMES", 1)   # 測試不追求真的連拍出上百幀

    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    monkeypatch.setattr(minimap, "_grab_window", lambda: frame)

    rate_calls = []
    monkeypatch.setattr(wgc, "request_full_rate", lambda on: rate_calls.append(on))

    captured = {}

    def _fake_solve(frames, boxes4):
        captured["n_frames"] = len(frames)
        captured["boxes4"] = boxes4
        return ["up", "down", "left", "right"]
    monkeypatch.setattr(rune_wheel, "solve", _fake_solve)

    boxes4 = [(50, 50, 90, 90), (120, 52, 160, 92), (190, 51, 230, 91), (260, 50, 300, 90)]
    dirs, err = rune._solve_wheel(boxes4)

    assert dirs == ["up", "down", "left", "right"]
    assert err == ""
    # request_full_rate(True) 開頭、request_full_rate(False) 結尾,引用計數平衡
    assert rate_calls[0] is True
    assert rate_calls[-1] is False
    assert rate_calls.count(True) == rate_calls.count(False)

    # boxes4 傳給 rune_wheel.solve() 時要相對「四支聯集裁切框」平移過,不是整幀座標
    pad = rune.WHEEL_BOX_PAD
    ux0 = min(b[0] for b in boxes4) - pad
    uy0 = min(b[1] for b in boxes4) - pad
    expect_rel = [(b[0] - ux0, b[1] - uy0, b[2] - ux0, b[3] - uy0) for b in boxes4]
    assert captured["boxes4"] == expect_rel
    assert captured["n_frames"] > 0


def test_solve_wheel_restores_full_rate_even_when_solve_raises(monkeypatch):
    """burst 抓幀或 rune_wheel.solve() 中途出錯,也要還原全速擷取的引用計數——
    不還原的話擷取會一直全速跑,吃掉一顆核心 66% 的代價會賴著不走(wgc.py 的
    註解記過這件事)。"""
    import minimap
    import rune_wheel
    import wgc

    monkeypatch.setattr(rune, "WHEEL_CAPTURE_SECS", 0.03)
    monkeypatch.setattr(rune, "WHEEL_POLL_GAP", 0.005)
    monkeypatch.setattr(rune, "WHEEL_MIN_FRAMES", 1)
    monkeypatch.setattr(minimap, "_grab_window", lambda: np.zeros((100, 100, 3), np.uint8))

    rate_calls = []
    monkeypatch.setattr(wgc, "request_full_rate", lambda on: rate_calls.append(on))

    def _boom(frames, boxes4):
        raise RuntimeError("模擬 solve() 內部錯誤")
    monkeypatch.setattr(rune_wheel, "solve", _boom)

    boxes4 = [(10, 10, 30, 30), (50, 10, 70, 30), (90, 10, 110, 30), (130, 10, 150, 30)]
    with pytest.raises(RuntimeError):
        rune._solve_wheel(boxes4)

    assert rate_calls[0] is True
    assert rate_calls[-1] is False


# ---------- _detect:接線邏輯(rotating -> wheel;非 rotating 行為不變) ----------
def test_detect_uses_wheel_result_when_rotating(monkeypatch):
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: ([], "1 線 5 幀未取得一致答案", 5, None, True, [(0, 0, 10, 10)] * 4))
    monkeypatch.setattr(rune, "_solve_wheel", lambda boxes4: (["up", "left", "down", "right"], ""))

    dirs, err, ms = rune._detect()

    assert dirs == ["up", "left", "down", "right"]
    assert err == ""
    assert rune._last["line"] == "wheel"


def test_detect_falls_back_to_claude_when_wheel_fails(monkeypatch):
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: ([], "1 線 5 幀未取得一致答案", 5, None, True, [(0, 0, 10, 10)] * 4))
    monkeypatch.setattr(rune, "_solve_wheel", lambda boxes4: ([], "旋轉路徑判不出方向"))
    monkeypatch.setattr(rune, "_write_crop", lambda f: ("dummy.png", ""))
    monkeypatch.setattr(rune._w(), "ask", lambda path: (["up", "up", "up", "up"], ""))

    dirs, err, ms = rune._detect()

    assert dirs == ["up", "up", "up", "up"]
    assert rune._last["line"] == "claude"


def test_detect_static_disagreement_without_claude_line_matches_old_error(monkeypatch):
    """非旋轉款、CV 讀不一致、2 線也沒開 —— 錯誤訊息與行為要跟改動前完全一致
    (「靜態路徑行為不得改變」的直接斷言)。"""
    monkeypatch.setattr(rune, "_line_claude", False)
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: ([], "1 線 5 幀未取得一致答案(最多票 xxx)", 5, None, False, None))

    def _boom(boxes4):
        raise AssertionError("非旋轉款不該呼叫 _solve_wheel")
    monkeypatch.setattr(rune, "_solve_wheel", _boom)

    dirs, err, ms = rune._detect()

    assert dirs == []
    assert "2 線未開啟,無法退線" in err
    assert rune._last["line"] == "cv"


def test_detect_ignores_wheel_when_kill_switch_off(monkeypatch):
    """MAPLE_RUNE_WHEEL=0(WHEEL_ENABLED=False)時,即使判定為旋轉款也不該走
    旋轉路徑 —— 直接照原本「CV 失敗」的行為退線。"""
    monkeypatch.setattr(rune, "WHEEL_ENABLED", False)
    monkeypatch.setattr(rune, "_line_claude", False)
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: ([], "1 線 5 幀未取得一致答案", 5, None, True, [(0, 0, 10, 10)] * 4))

    def _boom(boxes4):
        raise AssertionError("關閉開關時不該呼叫 _solve_wheel")
    monkeypatch.setattr(rune, "_solve_wheel", _boom)

    dirs, err, ms = rune._detect()
    assert dirs == []
    assert "2 線未開啟,無法退線" in err
