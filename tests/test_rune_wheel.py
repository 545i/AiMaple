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


# ---------- 「這是不是解放輪」判別(真實影像) ----------
# 【期望值的來源】人工目視確認過的畫面內容:
#   wheel  = 畫面上緣有「解放輪」橫幅、提示文字是「找尋箭頭晃動的方向並依序輸入方向鍵。」
#            (wheel0 就是 2026-08-19 21:24 那次判錯的實機畫面)
#   static = 舊款符文,提示文字是「要想解放符文,請按順序輸入方向鍵。」,沒有橫幅
#            (static0 是同一天 21:08 靠靜態判向解掉、紫標消失驗證過的那顆)
# 存的是橫幅四周 80x300 的裁切(整幀 1368x800 存 8 張會有數 MB),
# looks_like_wheel 對小圖不分層(rune_cv.search_band 的行為),整張都會找。
import os

import cv2
import numpy as np
import pytest

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rune_wheel_title_ref.npz")


@pytest.fixture(scope="module")
def title_ref():
    if not os.path.exists(REF):
        pytest.skip("缺 tests/rune_wheel_title_ref.npz")
    d = np.load(REF)
    return {k[4:]: cv2.imdecode(d[k], cv2.IMREAD_COLOR) for k in d.files}


@pytest.mark.parametrize("key", ["wheel0", "wheel1", "wheel2", "wheel3"])
def test_looks_like_wheel_true_on_real_wheel_frames(title_ref, key):
    ok, score = rw.looks_like_wheel(title_ref[key])
    assert ok, f"{key} 分數只有 {score:.3f}(門檻 {rw.TITLE_MIN_SCORE})"


@pytest.mark.parametrize("key", ["static0", "static1", "static2", "static3"])
def test_looks_like_wheel_false_on_real_old_rune_frames(title_ref, key):
    ok, score = rw.looks_like_wheel(title_ref[key])
    assert not ok, f"{key} 被誤判成解放輪(分數 {score:.3f})"


def test_title_score_margin_is_not_marginal(title_ref):
    """兩類的分數要真的分得開,不是靠門檻剛好卡在中間。

    全量驗收(不在測試裡跑,素材不進版控):解放輪 260 張 held-out 最低 0.324、
    p1=0.567;舊符文 364 張(高度夠、含得下橫幅的)最高 0.420。這裡守的是
    「手上這 8 張的最差情況仍有明顯間隔」,門檻若被改到夾在中間就會紅。"""
    wheel = [rw.title_score(title_ref[k]) for k in ("wheel0", "wheel1", "wheel2", "wheel3")]
    static = [rw.title_score(title_ref[k]) for k in ("static0", "static1", "static2", "static3")]
    assert min(wheel) - max(static) > 0.3
    assert max(static) < rw.TITLE_MIN_SCORE < min(wheel)


def test_title_score_returns_minus_one_on_tiny_image():
    """比模板還小的圖(單元測試常用的 dummy frame)不能讓 matchTemplate 拋例外。"""
    assert rw.title_score(np.zeros((10, 10, 3), dtype=np.uint8)) == -1.0
    assert rw.looks_like_wheel(None) == (False, -1.0)


# ---------- 5. 靜止箭頭改吃模型類別(angle_of 的「紅頭綠尾」假設對它們無效) ----------
# 【為什麼需要這一組】rune_collect 300 輪真實解放輪的量測(2026-08-20):
#   * 每一輪都是【2 支旋轉 + 2 支靜止】的混合款,不是四支全轉。
#   * 旋轉那 591 支 angle_of 全部讀得到(中位淨轉角 571°)。
#   * 靜止那 594 支只有 214 支讀得到;另外 380 支在【整段 ~200 幀裡一幀都讀不到】
#     ——它們的漸層跑到色環另一側(綠→青→藍→洋紅),沒有紅端或沒有綠端,
#     angle_of 的「紅頭綠尾」假設整支失效,不是偶爾失手。
#   * 結果 solve_from_angles 只有 38/297 = 12.8% 給得出答案。
# 而 rune_detr 在定位這四支框的時候【本來就已經算出方向類別】(rune_cv 走靜態
# 符文用的就是它,115 筆未參與訓練樣本上單支 95.0%,色度分割只有 86.5%),
# 解放輪這條路徑卻整個丟掉沒用。以下四個測試守住「靜止吃模型、旋轉不吃模型」。
def test_solve_from_angles_uses_model_label_when_angle_is_unreadable():
    """靜止箭頭整段讀不出角度時,方向改吃模型類別 —— 不能因為它整輪失敗。"""
    n = 120
    frame_idxs = list(range(n))
    unreadable = [None] * n
    rotating_down = _rotating_with_wobbles(
        n, start=15.0, rate=-13.0, wobble_frames=[20, 50, 80, 108], target_angle=270.0)
    rotating_left = _rotating_with_wobbles(
        n, start=200.0, rate=-11.0, wobble_frames=[15, 45, 75, 105], target_angle=180.0)
    labels = ["up", "right", "down", "left"]

    result = rw.solve_from_angles(
        [unreadable, unreadable, rotating_down, rotating_left], frame_idxs, labels=labels)

    assert result == ["up", "right", "down", "left"], result


def test_solve_from_angles_prefers_model_label_over_chroma_for_static_arrow():
    """靜止箭頭就算讀得到角度,也以模型類別為準(單支 95.0% vs 色度 86.5%)。
    這裡刻意讓色度算出的角度(90°=up)與模型類別(left)衝突,答案要是模型的。"""
    n = 60
    frame_idxs = list(range(n))
    angs = [_static(n, 90.0)] + [_static(n, a) for a in (0.0, 180.0, 270.0)]
    labels = ["left", "right", "left", "down"]

    result = rw.solve_from_angles(angs, frame_idxs, labels=labels)

    assert result[0] == "left", result


def test_solve_from_angles_ignores_model_label_for_rotating_arrow():
    """旋轉中的箭頭【不能】吃模型類別:線上模型只有四個方向類別、沒有 rot,
    它會硬給旋轉箭頭一個瞬間方向,而那個值對持續旋轉的箭頭沒有意義。
    答案必須來自晃動偵測。"""
    n = 120
    frame_idxs = list(range(n))
    rotating_down = _rotating_with_wobbles(
        n, start=15.0, rate=-13.0, wobble_frames=[20, 50, 80, 108], target_angle=270.0)
    rotating_left = _rotating_with_wobbles(
        n, start=200.0, rate=-11.0, wobble_frames=[15, 45, 75, 105], target_angle=180.0)
    rotating_up = _rotating_with_wobbles(
        n, start=40.0, rate=-12.0, wobble_frames=[18, 48, 78, 106], target_angle=90.0)
    rotating_right = _rotating_with_wobbles(
        n, start=310.0, rate=-14.0, wobble_frames=[22, 52, 82, 110], target_angle=0.0)
    wrong_labels = ["up", "up", "up", "up"]     # 模型對旋轉箭頭給的瞬間方向,全錯

    result = rw.solve_from_angles(
        [rotating_down, rotating_left, rotating_up, rotating_right],
        frame_idxs, labels=wrong_labels)

    assert result == ["down", "left", "up", "right"], result


def test_solve_from_angles_returns_none_when_unreadable_arrow_has_no_label():
    """讀不到角度、模型也沒給類別時,回 None —— 不能自己編一個方向出來。
    按錯方向鍵的代價是白燒一次符文冷卻,寧可整輪重來。"""
    n = 120
    frame_idxs = list(range(n))
    unreadable = [None] * n
    rotating = _rotating_with_wobbles(
        n, start=15.0, rate=-13.0, wobble_frames=[20, 50, 80, 108], target_angle=270.0)
    labels = [None, "right", "down", "left"]

    assert rw.solve_from_angles(
        [unreadable, unreadable, rotating, rotating], frame_idxs, labels=labels) is None
