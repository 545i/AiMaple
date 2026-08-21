# -*- coding: utf-8 -*-
"""連續解不掉符文時,不能默默放行讓巡邏繼續跑 —— 要強制暫停巡航等人處理。

【為什麼不是「一放棄就暫停」】那是更早的行為,而且被實測推翻過:紫標不會自己
消失,人重開巡邏後兜底看到同一個紫標又立刻停,整晚停擺(見 rune.py 放棄分支的
註解)。所以【單次】放棄維持現狀:記下位置、叫 navigator 放行、照常接回巡邏。
這裡要守的是【連續】放棄 —— 那代表這張圖/這個版本的符文我們根本解不掉,繼續
掛著只是白刷,必須停下來讓人知道。
"""
import pytest

import navigator
import rune


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(rune, "_giveup", {"pos": None, "at": 0.0, "streak": 0})
    yield


# ---------- navigator:無條件強制暫停(與 pause_purple 不同) ----------
def test_force_pause_stops_even_when_no_purple_on_screen(monkeypatch):
    """pause_purple() 在「場上沒有(非放行中的)紫標」時會直接 return 不停 ——
    而符文放行後正是這個狀態,所以連續失敗不能靠它。force_pause 必須無條件停。"""
    stopped = []
    monkeypatch.setattr(navigator, "_purple_present", lambda: False)
    monkeypatch.setattr(navigator, "stop", lambda: stopped.append(True))
    monkeypatch.setitem(navigator._state, "running", True)

    navigator.force_pause("符文連續解不掉")

    assert stopped == [True]
    assert navigator._state["phase"] == "forced_pause"
    assert "符文連續解不掉" in navigator._state["error"]


# ---------- rune:連續放棄的計數 ----------
def test_giveup_streak_below_threshold_does_not_force_pause():
    """還沒連續到門檻:維持現行行為(放行 + 接回巡邏),不要動不動就停機。"""
    for _ in range(rune.GIVEUP_STREAK_MAX - 1):
        assert rune._record_giveup() is False


def test_giveup_streak_at_threshold_forces_pause():
    """連續放棄達到門檻 → 要求強制暫停。"""
    for _ in range(rune.GIVEUP_STREAK_MAX - 1):
        rune._record_giveup()
    assert rune._record_giveup() is True


def test_solving_a_rune_resets_the_streak():
    """中間成功解掉一次就代表「解得掉」,連續計數要歸零,不能累積到誤停機。"""
    for _ in range(rune.GIVEUP_STREAK_MAX - 1):
        rune._record_giveup()
    rune.reset_giveup_streak()
    assert rune._record_giveup() is False


# ---------- 接線:_solve_flow 收場時真的會強制暫停 ----------
def _stub_flow(monkeypatch, calls):
    """把 _solve_flow 需要的外部相依全部換掉,讓它跑完一次【失敗】的流程。"""
    class _Nav:
        def stop(self): pass
        def status(self): return {"running": True}
        def ignore_purple_at(self, *a): calls.append(("ignore", a))
        def clear_purple_ignore(self): pass
        def pause_purple(self): calls.append(("pause_purple",))
        def force_pause(self, reason): calls.append(("force_pause", reason))

    class _Kb:
        def key_up(self, k): pass

    monkeypatch.setattr(rune, "_navigator", _Nav())
    monkeypatch.setattr(rune, "_keyboard", _Kb())
    monkeypatch.setattr(rune, "_resume_fn", lambda: calls.append(("resume",)))
    monkeypatch.setattr(rune, "_user_stopping", lambda: False)
    monkeypatch.setattr(rune, "_sleep_or_stop", lambda *a, **k: False)
    monkeypatch.setattr(rune, "_purple_now", lambda: [{"x": 10, "y": 20}])
    monkeypatch.setattr(rune, "_attempt", lambda px, py: False)   # 每次都解不掉
    monkeypatch.setattr(rune, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(rune, "ATTEMPT_GAP", 0.0)


def test_solve_flow_resumes_patrol_on_a_single_giveup(monkeypatch):
    """單次放棄:維持現行行為 —— 放行 + 接回巡邏,不強制暫停。"""
    calls = []
    _stub_flow(monkeypatch, calls)
    rune._busy.acquire(blocking=False)
    rune._solve_flow([(10, 20)], resume=True)

    kinds = [c[0] for c in calls]
    assert "resume" in kinds
    assert "force_pause" not in kinds


def test_solve_flow_forces_pause_after_consecutive_giveups(monkeypatch):
    """連續放棄到門檻:不接回巡邏,改成強制暫停巡航。"""
    calls = []
    _stub_flow(monkeypatch, calls)
    for _ in range(rune.GIVEUP_STREAK_MAX):
        rune._busy.acquire(blocking=False)
        rune._solve_flow([(10, 20)], resume=True)

    kinds = [c[0] for c in calls]
    assert "force_pause" in kinds
    # 最後一次不能又把巡邏接回去,否則等於沒停
    assert kinds[-1] == "force_pause"
