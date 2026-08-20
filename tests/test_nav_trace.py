# -*- coding: utf-8 -*-
"""server/nav_trace.py 的測試 —— 純邏輯,不需要遊戲/小地圖。

畫圖(render)不在這裡測:它要真的小地圖影格,而且「畫得對不對」只有看圖才算數
(今天已經被「指標漂亮但畫出來是錯的」坑過兩次)。這裡守的是記錄本身:
分類、起訖、上限、存檔與輪替。
"""
import json
import os

import pytest

import nav_trace as nt


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每個測試用自己的目錄,不要碰到真的 logs/nav_trace,也不要互相汙染。"""
    monkeypatch.setattr(nt, "TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(nt, "_run", None)
    monkeypatch.setattr(nt, "_last_run", None)
    yield


# ---------- 分類 ----------
def test_category_maps_navigator_phases():
    """類別直接對應 navigator._state["phase"],不另外發明一套。"""
    assert nt.category("g_walk") == "walk"
    assert nt.category("move_x") == "walk"
    assert nt.category("fine_x") == "walk"
    assert nt.category("g_rope") == "rope"
    assert nt.category("rope_up") == "rope"
    assert nt.category("g_fall") == "fall"
    assert nt.category("fall_adjust") == "fall"
    assert nt.category("g_jump") == "jump"


def test_category_deblock_wins_over_substrings():
    """脫困要排在最前面比對 —— 它是獨立的狀態,不該被其他關鍵字搶走。"""
    assert nt.category("deblock") == "deblock"


def test_category_unknown_is_other_not_a_guess():
    """認不得的 phase 一律 other。猜錯類別會讓軌跡圖說謊,寧可標成未知。"""
    assert nt.category("settle") == "other"
    assert nt.category("") == "other"
    assert nt.category(None) == "other"


# ---------- 記錄 ----------
def test_sample_and_event_only_recorded_between_start_and_finish():
    """沒開始就不該記 —— 否則巡邏之外的零星讀值會混進上一趟的軌跡裡。"""
    nt.sample(1, 2, "g_walk")
    assert nt.latest() is None
    nt.start("patrol", (10, 20))
    nt.sample(1, 2, "g_walk")
    nt.event("press", "c", "g_rope")
    r = nt.latest()
    assert len(r["samples"]) == 1 and len(r["events"]) == 1
    assert r["target"] == [10, 20]
    nt.finish(arrived=True)
    nt.sample(3, 4, "g_walk")                     # 結束後的讀值不該再進去
    assert len(nt.latest()["samples"]) == 1


def test_sample_records_phase_and_category_together():
    """記 phase 也記類別:phase 是原始證據,類別是畫圖用的;只留一個都不夠。"""
    nt.start("move")
    nt.sample(5, 6, "fall_adjust")
    s = nt.latest()["samples"][0]
    assert s["phase"] == "fall_adjust" and s["cat"] == "fall"
    assert s["x"] == 5 and s["y"] == 6


def test_start_twice_flushes_the_previous_run():
    """重複 start(例如上一趟異常中斷)不能把前一趟弄丟,要先收好再開新的。"""
    nt.start("patrol", (1, 1))
    nt.sample(1, 1, "g_walk")
    nt.start("patrol", (2, 2))
    files = os.listdir(nt.TRACE_DIR)
    assert len(files) == 1, "前一趟沒有被存檔"
    assert nt.latest()["target"] == [2, 2]


def test_finish_writes_file_with_duration_and_arrived():
    nt.start("patrol", (7, 8))
    nt.sample(1, 1, "g_walk")
    nt.finish(arrived=False)
    files = os.listdir(nt.TRACE_DIR)
    assert len(files) == 1
    with open(os.path.join(nt.TRACE_DIR, files[0]), encoding="utf-8") as f:
        rec = json.loads(f.readline())
    assert rec["arrived"] is False
    assert rec["target"] == [7, 8]
    assert "dur" in rec


def test_sample_cap_stops_growth_on_a_stuck_navigation():
    """卡住的導航會一直讀值。有上限才不會把記憶體吃光。"""
    nt.start("patrol")
    for i in range(nt.MAX_SAMPLES + 50):
        nt.sample(i % 100, 1, "g_walk")
    assert len(nt.latest()["samples"]) == nt.MAX_SAMPLES


def test_prune_keeps_only_recent_runs(monkeypatch):
    monkeypatch.setattr(nt, "KEEP_RUNS", 3)
    for i in range(6):
        nt.start("patrol", (i, i))
        nt.sample(i, i, "g_walk")
        # 檔名是秒級時間戳,同一秒內會互相覆蓋 —— 測試自己指定不同檔名
        nt.finish(arrived=True)
        os.rename(os.path.join(nt.TRACE_DIR, sorted(os.listdir(nt.TRACE_DIR))[-1]),
                  os.path.join(nt.TRACE_DIR, f"2026010{i}-000000.jsonl"))
    nt._prune()
    left = sorted(os.listdir(nt.TRACE_DIR))
    assert len(left) == 3
    assert left == ["20260103-000000.jsonl", "20260104-000000.jsonl",
                    "20260105-000000.jsonl"], "留下來的應該是最新的三趟"


def test_segment_records_intent_and_outcome():
    """點到點那一段要同時留下 target(意圖)與 end(實際),畫圖才對照得起來。"""
    nt.start("patrol")
    nt.segment("fall", (10, 20), [10, 30], (10, 31))
    seg = nt.latest()["segments"][0]
    assert seg["act"] == "fall"
    assert seg["target"] == [10, 30] and seg["end"] == [10, 31]


def test_latest_returns_a_copy_not_the_live_buffer():
    """回的是快照。呼叫端(端點序列化)拿到 live buffer 的話,導航還在寫就會
    在序列化途中被改動。"""
    nt.start("patrol")
    nt.sample(1, 1, "g_walk")
    snap = nt.latest()
    nt.sample(2, 2, "g_walk")
    assert len(snap["samples"]) == 1
