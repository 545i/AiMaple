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


# ---------- 橫幅判到解放輪時的優先路徑(2026-08-19 的實機失敗) ----------
# 【這組測試在守什麼】原本旋轉分支的前提是「1 線讀不出一致答案」,但旋轉的箭頭
# 每 0.12 秒轉約 48°、判向只有 90° 一格,五幀撞到同一格是常態 —— 1 線會自信地
# 給出錯答案,3 秒連拍永遠不會執行(實機 21 輪的 by_line 裡一次 wheel 都沒有)。
# 所以判別必須搶在 1 線之前,而且判到之後不准退回 1/2 線。
def _wheel_frame(monkeypatch, is_wheel, score=0.9):
    import rune_wheel
    monkeypatch.setattr(rune_wheel, "looks_like_wheel", lambda f: (is_wheel, score))


def test_detect_takes_wheel_path_before_cv_when_title_matches(monkeypatch):
    """判到「解放輪」橫幅 → 直接走旋轉路徑,連 _cv_read 都不該被呼叫。"""
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    _wheel_frame(monkeypatch, True)
    monkeypatch.setattr(rune, "_wheel_path", lambda f: (["up", "left", "down", "right"], ""))

    def _boom(f):
        raise AssertionError("判到解放輪時不該先讓 1 線靜態判向亂猜")
    monkeypatch.setattr(rune, "_cv_read", _boom)

    dirs, err, ms = rune._detect()

    assert dirs == ["up", "left", "down", "right"]
    assert err == ""
    assert rune._last["line"] == "wheel"
    assert rune._last["wheel"] == "ok"
    assert rune._last["wheel_score"] == 0.9


def test_detect_does_not_fall_back_to_static_lines_when_wheel_path_fails(monkeypatch):
    """判到是解放輪但旋轉路徑失敗 —— 【不准】退回 1/2 線。兩條線讀的都是單一
    瞬間的箭頭指向,對持續旋轉的箭頭沒有意義,按下去等於拿 1/256 賭符文冷卻。"""
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    _wheel_frame(monkeypatch, True)
    monkeypatch.setattr(rune, "_wheel_path", lambda f: ([], "旋轉路徑判不出方向"))

    def _boom_cv(f):
        raise AssertionError("解放輪失敗時不該退回 1 線")
    def _boom_crop(f):
        raise AssertionError("解放輪失敗時不該退回 2 線")
    monkeypatch.setattr(rune, "_cv_read", _boom_cv)
    monkeypatch.setattr(rune, "_write_crop", _boom_crop)

    dirs, err, ms = rune._detect()

    assert dirs == []
    assert err == "旋轉路徑判不出方向"
    assert rune._last["line"] == "wheel"


def test_detect_keeps_static_path_when_title_does_not_match(monkeypatch):
    """沒有橫幅(舊款符文)→ 完全走原本的靜態路徑,新程式碼不得插手。"""
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))
    _wheel_frame(monkeypatch, False, score=0.21)
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: (["up", "down", "left", "right"], "", 2, frame, False, None))

    def _boom(f):
        raise AssertionError("沒判到橫幅時不該走旋轉路徑")
    monkeypatch.setattr(rune, "_wheel_path", _boom)

    dirs, err, ms = rune._detect()

    assert dirs == ["up", "down", "left", "right"]
    assert rune._last["line"] == "cv"
    assert rune._last["wheel_score"] == 0.21


def test_detect_skips_title_check_when_kill_switch_off(monkeypatch):
    """MAPLE_RUNE_WHEEL=0 時連判別都不做,行為與加這段之前一致。"""
    monkeypatch.setattr(rune, "WHEEL_ENABLED", False)
    frame = _dummy_frame()
    monkeypatch.setattr(rune, "_grab_frame", lambda: (frame, True, ""))

    import rune_wheel
    def _boom(f):
        raise AssertionError("關閉開關時不該做橫幅判別")
    monkeypatch.setattr(rune_wheel, "looks_like_wheel", _boom)
    monkeypatch.setattr(
        rune, "_cv_read",
        lambda f: (["up", "down", "left", "right"], "", 2, frame, False, None))

    dirs, err, ms = rune._detect()
    assert dirs == ["up", "down", "left", "right"]
    assert rune._last["line"] == "cv"


def test_wheel_path_reports_failure_when_arrows_not_located(monkeypatch):
    """_wheel_path:定位不到穩定的 4 支箭頭時,要在連拍 3 秒【之前】就失敗。"""
    import minimap
    frame = _dummy_frame()
    monkeypatch.setattr(minimap, "_grab_window", lambda: frame)
    monkeypatch.setattr(rune, "WHEEL_BOX_GAP", 0.0)
    monkeypatch.setattr(rune, "_wheel_boxes", lambda frames: None)

    def _boom(boxes4):
        raise AssertionError("定位失敗就不該進入 3 秒連拍")
    monkeypatch.setattr(rune, "_solve_wheel", _boom)

    dirs, err = rune._wheel_path(frame)
    assert dirs == []
    assert "定位不到穩定的 4 支箭頭" in err


def test_wheel_path_passes_averaged_boxes_to_solver(monkeypatch):
    """定位成功時,_rotating_signal 產生的平均框要原封不動交給 _solve_wheel,
    而且用來定位的幀數要是 WHEEL_BOX_FRAMES(單幀給不出穩定度)。"""
    import minimap
    frame = _dummy_frame()
    seen = {}
    monkeypatch.setattr(minimap, "_grab_window", lambda: frame)
    monkeypatch.setattr(rune, "WHEEL_BOX_GAP", 0.0)
    boxes = [(1.0, 2.0, 3.0, 4.0)] * 4

    def _boxes(frames):
        seen["n"] = len(frames)
        return boxes
    monkeypatch.setattr(rune, "_wheel_boxes", _boxes)
    monkeypatch.setattr(rune, "_solve_wheel",
                        lambda b: (["down", "down", "up", "up"], "") if b is boxes else ([], "框不對"))

    dirs, err = rune._wheel_path(frame)
    assert dirs == ["down", "down", "up", "up"]
    assert err == ""
    assert seen["n"] == rune.WHEEL_BOX_FRAMES


def test_wheel_boxes_tolerates_frames_where_detection_misses(monkeypatch):
    """定位允許部分幀失敗:實機畫面會有怪物/傷害數字/名牌壓在箭頭上,要求每幀都
    選得出 4 支會讓忙碌畫面永遠進不了連拍(2026-08-19 21:24 那張就是這樣)。
    只要有 WHEEL_BOX_MIN_HITS 幀選得出來就該定位成功。"""
    import rune_detr
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    good = [(10.0, 10.0, 20.0, 20.0), (30.0, 10.0, 40.0, 20.0),
            (50.0, 10.0, 60.0, 20.0), (70.0, 10.0, 80.0, 20.0)]
    seq = [None, good, None, good, None]          # 5 幀只有 2 幀選得出 4 支
    it = iter(seq)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: next(it))

    boxes = rune._wheel_boxes([_dummy_frame()] * 5)
    assert boxes == good


def test_wheel_boxes_needs_at_least_two_hits(monkeypatch):
    """只有一幀選得出來就算不出「框穩不穩」,寧可失敗也不要拿沒驗證過的框去裁切。"""
    import rune_detr
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    good = [(10.0, 10.0, 20.0, 20.0)] * 4
    seq = [None, good, None]
    it = iter(seq)
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: next(it))
    assert rune._wheel_boxes([_dummy_frame()] * 3) is None


def test_wheel_boxes_rejects_jittering_boxes(monkeypatch):
    """框在幀間亂跳 = 定位跟著雜訊走,拿去裁切只會裁到別的東西 —— 要回 None。"""
    import rune_detr
    monkeypatch.setattr(rune_detr, "available", lambda: True)
    a = [(10.0, 10.0, 20.0, 20.0)] * 4
    b = [(10.0 + rune.ROTATE_BOX_JITTER * 3, 10.0, 20.0, 20.0)] * 4
    it = iter([a, b])
    monkeypatch.setattr(rune_detr, "detect_arrows", lambda f: next(it))
    assert rune._wheel_boxes([_dummy_frame()] * 2) is None
