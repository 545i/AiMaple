# -*- coding: utf-8 -*-
"""server/rune_wheel.py 的單元測試 —— 全部用合成的角度序列,不需要真的影片/圖片。

真實影片驗證(必須答出「下、左、下、上」)是 tools/eval_rune_wheel.py 的工作,
那支工具本身不是 pytest 測試(需要外部影片檔案、不適合塞進 CI),這裡只守
rune_wheel 內部的演算法邏輯:反轉偵測、同步假影過濾、圓形平均的邊界正確性、
以及【逐支獨立判斷靜止/旋轉】(同一顆符文可能同時有幾支靜止、幾支在轉,
不能假設四支同進退——手上的驗證影片四支剛好都在轉,混合情況沒有真實素材可驗,
只能靠這裡的合成測試守住,見 test_solve_from_angles_handles_mixed_static_and_rotating)。
"""
import rune_wheel as rw


def _rotating_with_wobbles(n, start, rate, wobble_frames, target_angle,
                           wobble_len=2, reverse_step=15.0):
    """合成一支【持續旋轉】箭頭的角度序列:大部分時間以 rate(度/幀)勻速轉動,
    但在 wobble_frames 列出的每個起始幀,先把角度快照成 target_angle,再用相反
    正負號的 reverse_step 走 wobble_len 幀(角速度反轉 = 晃動),之後恢復正常
    rate 繼續轉。回傳長度 n 的角度序列(度,0~360)。

    直接快照到 target_angle 是刻意的:重點是測演算法邏輯(反轉抓不抓得到、
    抓到的角度平不平均得對),不是重新模擬「箭頭轉到那個角度要花幾幀」這種
    物理細節。"""
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


def _static(n, angle, jitter=0.3):
    """合成一支【靜止】箭頭的角度序列:角度幾乎不變,只有很小的量測抖動。"""
    return [(angle + (jitter if i % 2 == 0 else -jitter)) % 360.0 for i in range(n)]


# ---------- 1. 角速度反轉能被偵測到 ----------
def test_find_wobbles_detects_angular_velocity_reversal():
    n = 90
    wobble_frames = [20, 50, 80]
    angs = _rotating_with_wobbles(n, start=0.0, rate=-13.0,
                                  wobble_frames=wobble_frames, target_angle=270.0)
    frame_idxs = list(range(n))

    events = rw.find_wobbles(angs, frame_idxs)

    assert len(events) == len(wobble_frames), (
        f"應該偵測到 {len(wobble_frames)} 次晃動,實際 {len(events)}: {events}")
    for f, a in events:
        assert abs(rw._circ_diff(a, 270.0)) < 10.0, (
            f"晃動事件角度 {a} 應該接近合成時設定的 270 度(容差 10 度)")


def test_find_wobbles_ignores_steady_rotation_without_reversal():
    """勻速旋轉、完全沒有反轉,不該偵測到任何晃動 —— 反面案例,防止函式對
    「持續轉動」本身就過度敏感。"""
    n = 60
    angs = [(-13.0 * i) % 360.0 for i in range(n)]
    frame_idxs = list(range(n))
    assert rw.find_wobbles(angs, frame_idxs) == []


# ---------- 2. 同步假影會被過濾 ----------
def test_drop_synced_wobbles_filters_simultaneous_artifact():
    """四支箭頭在【同一幀附近】都出現晃動事件 —— 那是畫面事件(場景切換/閃光)
    造成的假影,不是真的晃動,要整批濾掉;各自獨立、非同步的真晃動事件要保留。"""
    per_arrow_wobbles = [
        [(20, 90.0), (50, 91.0)],     # 50 幀是四支共有的「假影幀」
        [(25, 180.0), (50, 182.0)],
        [(30, 270.0), (50, 268.0)],
        [(35, 0.0), (50, 1.0)],
    ]

    filtered = rw.drop_synced_wobbles(per_arrow_wobbles)

    # 各自獨立的真事件都要保留
    assert filtered[0] == [(20, 90.0)]
    assert filtered[1] == [(25, 180.0)]
    assert filtered[2] == [(30, 270.0)]
    assert filtered[3] == [(35, 0.0)]
    # frame=50 的同步假影四支都要被濾掉
    for w in filtered:
        assert all(f != 50 for f, _a in w)


def test_drop_synced_wobbles_keeps_events_when_not_all_four_synced():
    """只有兩支箭頭在同一幀附近晃動(另外兩支這一幀沒有事件)—— 不算同步假影
    (規格明講要【四支都】同時晃才丟),兩個事件都要保留。"""
    per_arrow_wobbles = [
        [(50, 90.0)],
        [(50, 180.0)],
        [],
        [],
    ]
    filtered = rw.drop_synced_wobbles(per_arrow_wobbles)
    assert filtered[0] == [(50, 90.0)]
    assert filtered[1] == [(50, 180.0)]


# ---------- 3. 圓形平均在 0°/360° 交界處正確 ----------
def test_circ_mean_wraps_correctly_at_0_360_boundary():
    """350° 與 10° 的平均應該落在 0° 附近(取最短路徑走 20 度那條),不是算術
    平均的 180°(那條路是繞遠路,錯誤答案)。"""
    mean_a = rw.circ_mean([350.0, 10.0])
    assert mean_a is not None
    dist_to_zero = min(mean_a, 360.0 - mean_a)
    assert dist_to_zero < 1e-6, f"預期接近 0/360,實際 {mean_a}"


def test_circ_mean_simple_case_matches_arithmetic_when_no_wraparound():
    assert abs(rw.circ_mean([80.0, 100.0]) - 90.0) < 1e-6


# ---------- 4. 混合:同一顆符文裡有靜止也有旋轉的箭頭 ----------
def test_solve_from_angles_handles_mixed_static_and_rotating():
    """同一顆符文可能同時有幾支箭頭靜止、幾支在轉(使用者實測指出的關鍵事實,
    不能假設整顆符文要嘛全靜止要嘛全旋轉)。這裡合成 2 支靜止 + 2 支旋轉,
    solve_from_angles 要能各自獨立判斷、各自給出正確方向。

    手上唯一的真實驗證影片(tools/eval_rune_wheel.py)四支箭頭剛好都在轉,
    沒有混合情況的真實素材可驗 —— 這個合成測試是目前唯一守住「逐支獨立判斷」
    這個設計的地方,見 server/rune_wheel.py::solve_from_angles 開頭說明。"""
    n = 120
    frame_idxs = list(range(n))

    static_up = _static(n, angle=90.0)          # 箭頭 1:靜止,指向上
    static_right = _static(n, angle=2.0)        # 箭頭 2:靜止,指向右(貼近 0°邊界)
    rotating_down = _rotating_with_wobbles(     # 箭頭 3:持續旋轉,晃動於「下」
        n, start=15.0, rate=-13.0, wobble_frames=[20, 50, 80, 108], target_angle=270.0)
    rotating_left = _rotating_with_wobbles(     # 箭頭 4:持續旋轉,晃動於「左」
        n, start=200.0, rate=-11.0, wobble_frames=[15, 45, 75, 105], target_angle=180.0)

    result = rw.solve_from_angles(
        [static_up, static_right, rotating_down, rotating_left], frame_idxs)

    assert result == ["up", "right", "down", "left"], result


def test_solve_from_angles_all_static_still_works():
    """退化情況:四支全靜止(舊版符文的行為),同一套邏輯也要正確 —— 不要求
    只有旋轉款才 work。"""
    n = 40
    frame_idxs = list(range(n))
    angs = [_static(n, a) for a in (90.0, 0.0, 180.0, 270.0)]
    result = rw.solve_from_angles(angs, frame_idxs)
    assert result == ["up", "right", "left", "down"]


def test_solve_from_angles_returns_none_when_one_arrow_never_wobbles():
    """三支正常給出方向,一支持續旋轉卻【完全沒有】偵測到晃動事件(合成
    「勻速轉動、無反轉」模擬追蹤/量測失敗的情況)—— 判不出來就該回 None,
    不能硬湊三支的答案充當四支。"""
    n = 90
    frame_idxs = list(range(n))
    good = _rotating_with_wobbles(n, start=15.0, rate=-13.0,
                                  wobble_frames=[20, 50, 80], target_angle=90.0)
    never_wobbles = [(-13.0 * i) % 360.0 for i in range(n)]   # 勻速,無反轉
    angs = [good, good, good, never_wobbles]
    assert rw.solve_from_angles(angs, frame_idxs) is None


def test_circular_r_separates_static_from_rotating():
    """圓形集中度 R 是靜止/旋轉分類的判斷依據:靜止序列 R 要遠高於門檻,
    旋轉多圈的序列 R 要遠低於門檻,兩者不能落在同一側。"""
    n = 130
    static_r = rw._circular_r(_static(n, 90.0))
    rotating_r = rw._circular_r([(-13.0 * i) % 360.0 for i in range(n)])
    assert static_r >= rw.STATIC_R_MIN
    assert rotating_r < rw.STATIC_R_MIN
