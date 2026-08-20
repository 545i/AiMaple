# -*- coding: utf-8 -*-
"""繩索位置:規劃時用【記錄到的真實位置】,而不是猜重疊區中點。

【為什麼會有這支】50 趟軌跡實測:規劃出來的上繩 x 永遠是 29,而真正上得去的是
23~25(差 4~6 格)。因為 build_physics 在沒有繩索資料時,是拿「兩塊平台水平重疊區
的中點」當繩索位置 —— 但繩索是固定物件,跟重疊區中點沒有任何關係。
後果:每一次上繩的第一按都必定失敗(按 C 共 16 次,8 次沒反應,剛好一半),
要靠 _rope_to 沿 -4/+4/-8… 掃描才找得到,每次固定浪費約 2.3 秒(佔巡邏時間 6.4%)。
"""
import pathgraph


def test_rope_x_falls_back_to_midpoint_without_data():
    """沒有繩索資料時維持原本行為(猜中點)—— 沒記錄過的地圖不能因此走不動。"""
    assert pathgraph._rope_x(None, 10, 30) == 20
    assert pathgraph._rope_x([], 10, 30) == 20


def test_rope_x_uses_recorded_position_not_the_midpoint():
    """這就是 bug 的核心:中點 29、真正的繩索 24,要選 24。"""
    assert pathgraph._rope_x([{"x": 24}], 20, 38) == 24


def test_rope_x_ignores_ropes_outside_the_overlap():
    """不在這兩塊平台重疊區內的繩索連不到這條邊,不能拿來用。"""
    assert pathgraph._rope_x([{"x": 5}, {"x": 99}], 20, 38) == 29


def test_rope_x_picks_the_one_closest_to_the_middle():
    """有多條時取最靠近重疊區中央的 —— 貼著平台邊緣的那條站上去容易掉層。"""
    assert pathgraph._rope_x([{"x": 21}, {"x": 28}, {"x": 37}], 20, 38) == 28


def test_rope_x_tolerates_malformed_entries():
    """繩索清單是使用者資料/自動寫入混在一起,壞掉一筆不能讓整條路徑規劃不出來。"""
    assert pathgraph._rope_x([{"y": 3}, {"x": None}, {"x": 24}], 20, 38) == 24


# ---------- 記錄 ----------
def test_note_rope_records_and_dedups(tmp_path, monkeypatch):
    """同一條繩索每次上繩成功都會回報,不能每次都追加一筆。"""
    import mapdata
    monkeypatch.setattr(mapdata, "_dir", lambda: str(tmp_path), raising=False)
    store = {"ropes": []}
    monkeypatch.setattr(mapdata, "load", lambda mid: store)
    monkeypatch.setattr(mapdata, "save", lambda mid, d: store.update(d))

    assert mapdata.note_rope("m", 24) is True
    assert mapdata.note_rope("m", 24) is False, "同一位置不該重複記"
    assert mapdata.note_rope("m", 25) is False, "誤差 1 格視為同一條"
    assert mapdata.note_rope("m", 40) is True, "另一條繩索要記得起來"
    assert [r["x"] for r in store["ropes"]] == [24, 40]


def test_note_rope_marks_auto_so_it_is_distinguishable(tmp_path, monkeypatch):
    """自動學到的要標記出來,才分得清哪些是使用者手動標的。"""
    import mapdata
    store = {"ropes": []}
    monkeypatch.setattr(mapdata, "load", lambda mid: store)
    monkeypatch.setattr(mapdata, "save", lambda mid, d: store.update(d))
    mapdata.note_rope("m", 24)
    assert store["ropes"][0].get("auto") is True


# ---------- 連續上繩要併成一次 ----------
import navigator


def test_merge_rope_chain_collapses_consecutive_ropes():
    """規劃器把上繩建模成「爬到相鄰上一層」,跨兩層會排出兩段 rope。
    但 _rope_up 按一次 C 就【一路衝到頂】,停不下來 —— 照兩段跑會變成
    「爬到頂→掉回中間層→再爬到頂→再掉」,實測(20260820-143616)多花約 6 秒,
    而且下跳修正途中【就經過最終目標層】卻不停。併成一段:爬一次,直接修正到最後
    那一段的目標。"""
    path = [((30, 68), "walk"), ((29, 56), "rope"), ((29, 45), "rope"), ((26, 45), "walk")]
    out = navigator._merge_rope_chain(path)
    assert out == [((30, 68), "walk"), ((29, 45), "rope"), ((26, 45), "walk")]


def test_merge_rope_chain_keeps_single_rope_untouched():
    path = [((30, 68), "walk"), ((29, 56), "rope"), ((26, 56), "walk")]
    assert navigator._merge_rope_chain(path) == path


def test_merge_rope_chain_does_not_merge_across_other_moves():
    """中間夾了走位/下跳就不是同一次上繩,不能併 —— 併了會跳過那些動作。"""
    path = [((29, 56), "rope"), ((40, 56), "walk"), ((40, 45), "rope")]
    assert navigator._merge_rope_chain(path) == path


def test_merge_rope_chain_handles_empty_and_all_rope():
    assert navigator._merge_rope_chain([]) == []
    assert navigator._merge_rope_chain(
        [((1, 60), "rope"), ((1, 50), "rope"), ((1, 40), "rope")]) == [((1, 40), "rope")]
