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
