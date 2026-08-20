# -*- coding: utf-8 -*-
"""符文走位「提早停止」的測試(全部 monkeypatch,不需要遊戲/導航器)。

守的是使用者回報的那件事:導航一旦開始就走完整趟,即使角色早就經過了可按的
位置 —— 最壞一側三趟、兩側六趟,每趟 3~7 秒。現在走位途中會問「現在就按得到
了嗎」,是就立刻停。

【判定條件不能放寬】提早停止用的條件必須與 solve() 判定「本來就在環內」完全
相同(距離在環內 + 同層 + 同一塊平台)。若為了早點停而放寬,落點會跑到環外:
太遠按鍵沒反應(白費一次冷卻)、太近角色蓋住紫標(驗證失效會把失敗誤判成成功)。
"""
import pytest

import rune


class _Nav:
    """假導航器:running 由測試控制,記錄有沒有被 stop()。"""

    def __init__(self, polls_until_done=1000):
        self.polls = 0
        self.polls_until_done = polls_until_done
        self.stopped = False
        self._arrived = False

    def move_to(self, tx, ty, precise=False):
        self.target = (tx, ty)
        return True, "ok"

    def status(self):
        self.polls += 1
        running = self.polls < self.polls_until_done and not self.stopped
        return {"running": running, "arrived": self._arrived}

    def stop(self):
        self.stopped = True


@pytest.fixture
def nav(monkeypatch):
    n = _Nav()
    monkeypatch.setattr(rune, "_navigator", n)
    monkeypatch.setattr(rune, "EARLY_CHECK_GAP", 0.0)      # 測試不等
    return n


# ---------- _ready_here:判定條件 ----------
@pytest.fixture
def at(monkeypatch):
    """把角色放在指定位置,並讓 _same_platform 回指定值。"""
    def _set(pos, same=True):
        monkeypatch.setattr(rune, "_dot_now", lambda: pos)
        monkeypatch.setattr(rune, "_same_platform", lambda p, x, y: same)
    return _set


def test_ready_here_true_inside_ring_same_platform(at):
    at((100 + rune.RADIUS_MIN, 50))
    assert rune._ready_here(100, 50) is True


def test_ready_here_false_when_too_close(at):
    """太近會蓋住紫標,紫標消失的驗證就失效 —— 會把失敗誤判成解除成功。"""
    at((100 + rune.RADIUS_MIN - 1, 50))
    assert rune._ready_here(100, 50) is False


def test_ready_here_false_when_too_far(at):
    """太遠按啟動鍵不會有反應,白費一次符文冷卻。"""
    at((100 + rune.RADIUS_MAX + 1, 50))
    assert rune._ready_here(100, 50) is False


def test_ready_here_false_on_other_platform(at):
    """高度相近但不是同一塊平台 —— 實測按鍵不會有反應,不能算到位。"""
    at((100 + rune.RADIUS_MIN, 50), same=False)
    assert rune._ready_here(100, 50) is False


def test_ready_here_false_on_wrong_level(at):
    at((100 + rune.RADIUS_MIN, 50 + rune.SAME_LEVEL_DY + 1))
    assert rune._ready_here(100, 50) is False


def test_ready_here_false_without_dot(monkeypatch):
    monkeypatch.setattr(rune, "_dot_now", lambda: None)
    assert rune._ready_here(100, 50) is False


# ---------- _goto:提早停止 ----------
def test_goto_stops_navigation_when_early_becomes_true(nav, monkeypatch):
    monkeypatch.setattr(rune, "_settle_pause", lambda: None)
    calls = {"n": 0}

    def early():
        calls["n"] += 1
        return calls["n"] >= 2            # 第二次檢查時才進入可按範圍

    assert rune._goto(10, 20, early=early) is True
    assert nav.stopped is True, "進入可按範圍後應該要求導航停止"


def test_goto_without_early_keeps_old_behaviour(monkeypatch):
    """不給 early 時行為與加這個功能之前完全一樣:走完、回 arrived。"""
    n = _Nav(polls_until_done=3)
    n._arrived = True
    monkeypatch.setattr(rune, "_navigator", n)
    assert rune._goto(10, 20) is True
    assert n.stopped is False, "沒給 early 就不該主動停止導航"


def test_goto_ignores_a_broken_early_check(nav, monkeypatch):
    """檢查自己壞掉不能讓走位跟著壞 —— 照原本走完。"""
    monkeypatch.setattr(rune, "_settle_pause", lambda: None)
    nav.polls_until_done = 4
    nav._arrived = True

    def boom():
        raise RuntimeError("讀值壞了")

    assert rune._goto(10, 20, early=boom) is True
    assert nav.stopped is False


def test_goto_waits_for_the_character_to_settle_after_early_stop(nav, monkeypatch):
    """stop() 只是要求停止,角色還有慣性。不等停穩的話,接著的 _check_spot 會拿到
    移動中的位置 —— 那正是導航那邊「用瞬時值下判斷」踩過的同一個坑。"""
    waited = {"n": 0}
    monkeypatch.setattr(rune, "_settle_pause", lambda: waited.__setitem__("n", waited["n"] + 1))
    rune._goto(10, 20, early=lambda: True)
    assert waited["n"] == 1, "提早停止後沒有等它停穩"


# ---------- _check_spot:距離也要判(不是只看平台與層) ----------
@pytest.fixture
def spot(monkeypatch):
    """把角色放在指定位置,並讓平台/紫標判定都通過,只留距離這個變因。"""
    def _set(pos):
        monkeypatch.setattr(rune, "_dot_now", lambda: pos)
        monkeypatch.setattr(rune, "_purple_now", lambda: [(100, 50)])
        monkeypatch.setattr(rune, "_same_platform", lambda p, x, y: True)
    return _set


def test_check_spot_rejects_too_far(spot):
    """太遠按啟動鍵不會有反應,整輪白費還吃掉一次符文冷卻。
    實測 631 筆判定可按的裡面有 7~33 格的,因為原本這裡完全沒看距離。"""
    spot((100 + rune.RADIUS_MAX + 1, 50))
    ok, info = rune._check_spot(100, 50)
    assert ok is False and info["reason"] == "too_far"


def test_check_spot_rejects_too_close(spot):
    """太近角色會蓋住紫標,「紫標消失」的驗證失效 —— 失敗會被誤判成解除成功。"""
    spot((100 + rune.RADIUS_MIN - 1, 50))
    ok, info = rune._check_spot(100, 50)
    assert ok is False and info["reason"] == "too_close"


def test_check_spot_accepts_inside_the_ring(spot):
    for d in range(rune.RADIUS_MIN, rune.RADIUS_MAX + 1):
        spot((100 + d, 50))
        ok, info = rune._check_spot(100, 50)
        assert ok is True, f"距離 {d} 在環內卻被拒絕"
        assert info["reason"] == ""


def test_check_spot_still_rejects_other_platform_first(spot, monkeypatch):
    """站到隔壁平台時,原因要是 other_platform 而不是距離 —— 距離對了也沒用,
    錯誤訊息指錯方向會讓人去調環的大小,白忙一場。"""
    spot((100 + rune.RADIUS_MIN, 50))
    monkeypatch.setattr(rune, "_same_platform", lambda p, x, y: False)
    ok, info = rune._check_spot(100, 50)
    assert ok is False and info["reason"] == "other_platform"


def test_check_spot_uses_the_same_ring_as_ready_here(spot):
    """三處(solve 的 in_ring、走位途中的 _ready_here、這裡)必須用同一組數字,
    否則「走位途中說可以停」跟「停下來後說不行」會互相矛盾,原地打轉。"""
    for d in (rune.RADIUS_MIN - 1, rune.RADIUS_MIN, rune.RADIUS_MAX, rune.RADIUS_MAX + 1):
        spot((100 + d, 50))
        assert rune._check_spot(100, 50)[0] == rune._ready_here(100, 50), f"距離 {d} 兩處判定不一致"
