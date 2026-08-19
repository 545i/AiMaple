# -*- coding: utf-8 -*-
"""server/fiona_cv.py 的測試。

分兩層:
  1. 純邏輯(slot_of / viterbi_track / band_energy)用【合成】資料 —— 可以精確
     算出正確答案,不必依賴真實影像,也不會因為遊戲改版而壞。
  2. 影像元件(find_window / read_scoreboard / buttons_lit / find_arrow)用
     tests/fiona_ref.npz 裡的真實幀。

【期望值的來源】全部是人工目視確認過的畫面內容(對話中逐張放大看過的),不是拿
實作自己的輸出回填。這是本專案的鐵律:拿模型/實作的輸出當標籤等於固化錯誤
(見 server/rune_collect.py 開頭那段)。具體:
  - shuffle 幀(原片 frame 5350)計分格顯示「3 1」
  - answer  幀(原片 frame 5298)計分格顯示「3」,且四顆按鈕變藍
  - reveal  幀(原片 frame 110) 展示階段,橘色箭頭指槽 1
  - bands   是場3第3輪(初始槽 4 → 計分格真值槽 2)
"""
import os

import cv2
import numpy as np
import pytest

import fiona_cv as F

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fiona_ref.npz")


@pytest.fixture(scope="module")
def ref():
    if not os.path.exists(REF):
        pytest.skip("缺 tests/fiona_ref.npz")
    d = np.load(REF)
    out = {"bands": d["bands"]}
    for k in ("shuffle", "reveal", "answer", "nowin"):
        out[k] = cv2.imdecode(d["png_" + k], cv2.IMREAD_COLOR)
    return out


# ------------------------------------------------------------ 純邏輯(合成)
def test_slot_of_equal_split():
    """沒給 centers 時退回等分。舞台寬 413 → 每槽 103.25px。"""
    w = F.STAGE[2] - F.STAGE[0]
    assert F.slot_of(0, None, w) == 1
    assert F.slot_of(w - 1, None, w) == 4
    assert F.slot_of(w * 0.3, None, w) == 2
    assert F.slot_of(w * 0.6, None, w) == 3


def test_slot_of_uses_nearest_center():
    """給了 centers 就取最近的槽心 —— 聚光燈校準出來的間距不見得等分。"""
    centers = [50.0, 150.0, 260.0, 380.0]
    assert F.slot_of(55, centers) == 1
    assert F.slot_of(149, centers) == 2
    assert F.slot_of(300, centers) == 3
    assert F.slot_of(1000, centers) == 4


def test_slot_of_clamps_out_of_range():
    w = F.STAGE[2] - F.STAGE[0]
    assert F.slot_of(-999, None, w) == 1
    assert F.slot_of(10 ** 6, None, w) == 4


def _synth_energy(T, W, xs, sigma=6.0):
    """依給定的 x 軌跡合成能量圖:每幀在 xs[t] 放一個高斯峰。"""
    grid = np.arange(W)[None, :]
    return np.exp(-0.5 * ((grid - np.asarray(xs)[:, None]) / sigma) ** 2)


def test_viterbi_follows_synthetic_ramp_exactly_without_penalty():
    """λ=0:沒有平滑懲罰時,應該【精確】貼合斜線軌跡。

    這個測試守住遞推本身的正確性。它曾經抓出一個真 bug:平滑懲罰原本是在選完
    最小值【之後】才加(dp[t] += cost + lam*|bk|),等於選轉移時沒考慮懲罰。
    修正成在比較前加之後,終點誤差從 8px 降到 3px;λ=0 時則精確為 0。
    """
    T, W = 120, 413
    xs = np.linspace(50, 360, T)
    P = _synth_energy(T, W, xs)
    path = F.viterbi_track(P, x0=50, lam=0.0)
    assert abs(path[-1] - xs[-1]) <= 1
    assert abs(path[0] - xs[0]) <= 2
    assert np.abs(path - xs).max() <= 2


def test_viterbi_penalty_costs_a_little_tracking_lag():
    """λ>0 會讓路徑抄近路以省下位移懲罰,終點略為落後 —— 這是【設計上的取捨】
    不是錯誤:用貼合度換抗噪性。

    重點是這個落後必須遠小於槽寬(103px),否則會影響槽級判斷。實測 λ=0.10 時
    落後 3px,約 3% 槽寬。這裡把上限釘在 15px(≈15% 槽寬),λ 若被調到讓落後
    超過這個量,就該視為回歸。
    """
    T, W = 120, 413
    xs = np.linspace(50, 360, T)
    P = _synth_energy(T, W, xs)
    lag = xs[-1] - F.viterbi_track(P, x0=50, lam=F.SMOOTH_LAMBDA)[-1]
    assert 0 <= lag <= 15


def test_viterbi_respects_max_step():
    """轉移約束:相鄰幀位移不得超過 max_step,即使能量圖在誘導它瞬移。"""
    T, W = 40, 400
    xs = [30] * 20 + [370] * 20          # 中間直接瞬移,物理上不可能
    P = _synth_energy(T, W, xs)
    path = F.viterbi_track(P, x0=30, max_step=8)
    assert np.abs(np.diff(path)).max() <= 8


def test_viterbi_prefers_continuous_track_over_brighter_decoy():
    """有一條【更亮但不連續】的假軌跡時,應該仍跟著連續的那條走。

    這正是卡爾曼貪婪匹配會失手、而全域最優能救回來的情境:誘餌只在後半段出現
    且亮度更高,逐幀貪婪會被吸過去,但整段最優解不會 —— 因為跳過去要付出
    超過 max_step 的代價(無法到達),或大量平滑懲罰。
    """
    T, W = 100, 413
    xs = np.linspace(40, 200, T)
    P = _synth_energy(T, W, xs) * 0.7
    P[50:, 380] = 1.0                    # 遠處一條更亮但跳不過去的誘餌
    path = F.viterbi_track(P, x0=40)
    assert abs(path[-1] - xs[-1]) <= 5


def test_band_energy_peaks_track_moving_object():
    """合成:靜態紋理背景 + 一個橫向移動的亮塊。

    band_energy 應該把靜態背景減掉,只留移動物體 —— 每幀能量峰的位置要跟著
    亮塊走。這同時守住「高通 + 時間中位數背景減除」這兩層。
    """
    T, H, W = 40, 104, 413
    rng = np.random.default_rng(0)
    bg = rng.integers(0, 120, size=(H, W), dtype=np.uint8)   # 靜態紋理
    xs = np.linspace(60, 340, T)
    bands = []
    for t in range(T):
        f = bg.copy().astype(np.int32)
        x = int(xs[t])
        f[30:80, max(0, x - 20):x + 20] += 110
        bands.append(np.clip(f, 0, 255).astype(np.uint8))
    P = F.band_energy(bands)
    assert P is not None and P.shape == (T, W)
    for t in (5, 20, 35):
        assert abs(int(np.argmax(P[t])) - xs[t]) <= 25


def test_band_energy_needs_enough_frames():
    """幀數太少時中位數背景無意義,明確回 None 而不是給出垃圾。"""
    assert F.band_energy([np.zeros((104, 413), np.uint8)] * 4) is None


# ------------------------------------------------------------ 影像元件(真實幀)
def test_find_window_on_real_frames(ref):
    """三張有視窗的幀都該命中;素材是從完整幀裁出的,視窗固定落在 (100, 100)。"""
    for k in ("shuffle", "reveal", "answer"):
        win, score, _sc = F.find_window(ref[k])
        assert win == (100, 100), k
        assert score > 0.95, k


def test_find_window_rejects_frame_without_window(ref):
    """沒有謎題視窗時必須回 None,不能硬給一個最高分位置。"""
    win, score, _sc = F.find_window(ref["nowin"])
    assert win is None
    assert score <= F.TITLE_MATCH_MIN


def test_find_window_hint_gives_same_result(ref):
    """hint 是效能關鍵(全圖 48.6ms → 局部 2.7ms),但絕不能改變答案。

    三種情況都必須回同一個位置:hint 準確、hint 偏移、hint 完全錯誤(此時要能
    退回全圖搜尋,否則視窗被玩家拖走就永遠找不回來)。
    """
    f = ref["shuffle"]
    truth, _, _sc = F.find_window(f)
    assert truth is not None
    for hint in (truth,
                 (truth[0] - 30, truth[1] + 25),
                 (truth[0] + 40, truth[1] - 40),
                 (0, 0)):
        got, score, _sc = F.find_window(f, hint=hint, scale=1.0)
        assert got == truth, hint
        assert score > 0.95


def test_find_window_hint_does_not_invent_a_window(ref):
    """沒有視窗的幀,就算給了 hint 也必須回 None,不能被 hint 誘導出一個位置。"""
    got, _s, _sc = F.find_window(ref["nowin"], hint=(100, 100), scale=1.0)
    assert got is None


def test_find_window_handles_degenerate_input():
    assert F.find_window(None)[0] is None
    assert F.find_window(np.zeros((0, 0, 3), np.uint8))[0] is None
    assert F.find_window(np.zeros((5, 5, 3), np.uint8))[0] is None   # 比模板還小


def test_slot_centers_are_four_and_ordered(ref):
    """聚光燈固定不動,應該穩定給出四個由左到右的槽心。"""
    win, _, _sc = F.find_window(ref["shuffle"])
    c = F.slot_centers(ref["shuffle"], win)
    assert len(c) == 4
    assert all(c[i] < c[i + 1] for i in range(3))
    w = F.STAGE[2] - F.STAGE[0]
    assert 0 <= c[0] and c[-1] <= w
    # 四個槽大致等距(遊戲版面是均分的),容差放寬到 40%
    gaps = np.diff(c)
    assert gaps.max() / gaps.min() < 1.4


def test_buttons_lit_only_during_answer_window(ref):
    """按鈕變藍只發生在作答窗口 —— 這是切輪與觸發點擊的訊號。"""
    for k, expect in (("answer", True), ("shuffle", False), ("reveal", False)):
        win, _, _sc = F.find_window(ref[k])
        lit, frac = F.buttons_lit(ref[k], win)
        assert lit is expect, (k, frac)


def test_read_scoreboard_shuffle_frame(ref):
    """原片 frame 5350,目視確認計分格是「3 1」,後兩格空。"""
    win, _, _sc = F.find_window(ref["shuffle"])
    cells = F.read_scoreboard(ref["shuffle"], win)
    assert [c[0] for c in cells] == ["yellow", "yellow", "empty", "empty"]
    assert [c[1] for c in cells[:2]] == ["3", "1"]
    assert F.score_filled(cells) == 2


def test_read_scoreboard_answer_frame(ref):
    """原片 frame 5298,目視確認計分格只有第一格「3」。"""
    win, _, _sc = F.find_window(ref["answer"])
    cells = F.read_scoreboard(ref["answer"], win)
    assert cells[0][0] == "yellow" and cells[0][1] == "3"
    assert [c[0] for c in cells[1:]] == ["empty"] * 3
    assert F.score_filled(cells) == 1


def test_is_reveal_separates_phases(ref):
    """展示階段是白舞台,洗牌階段是暗背景 + 聚光燈,亮度占比就分得開。"""
    for k, expect in (("reveal", True), ("shuffle", False), ("answer", False)):
        win, _, _sc = F.find_window(ref[k])
        ok, frac = F.is_reveal(ref[k], win)
        assert ok is expect, (k, frac)


def test_find_arrow_points_to_slot_one(ref):
    """原片 frame 110,目視確認橘色箭頭指最左邊那隻(槽 1)。"""
    win, _, _sc = F.find_window(ref["reveal"])
    centers = F.slot_centers(ref["reveal"], win)
    slot, area = F.find_arrow(ref["reveal"], win, centers)
    assert slot == 1, (slot, area)
    assert area >= 25


def test_find_arrow_absent_during_shuffle(ref):
    """洗牌階段沒有箭頭。蘑菇身上的橘色蝴蝶結不該被誤認 —— 所以只搜上部 40%。"""
    win, _, _sc = F.find_window(ref["shuffle"])
    slot, _area = F.find_arrow(ref["shuffle"], win)
    assert slot is None


# ------------------------------------------------------------ 端到端
def test_end_to_end_tracks_real_round(ref):
    """真實一輪:場3第3輪,初始槽 4,計分格真值槽 2。

    走的是 production 會走的路(能量圖 → 全域最優路徑 → 槽),不是重刻的簡化版
    —— 評估必須走 production 入口,這個專案為此吃過大虧(見 MEMORY)。
    """
    bands = list(ref["bands"])
    assert len(bands) > 50
    P = F.band_energy(bands)
    assert P is not None
    w = P.shape[1]
    centers = [(k + 0.5) * w / 4 for k in range(4)]
    path = F.viterbi_track(P, centers[4 - 1])
    assert F.slot_of(float(path[-1]), centers, w) == 2
    assert np.abs(np.diff(path)).max() <= F.MAX_STEP_PX


def test_path_confidence_shape(ref):
    """path_confidence 目前只是統計量,不保證能預測失敗(見函式註解),
    這裡只守住它的輸出結構與值域。"""
    bands = list(ref["bands"])
    P = F.band_energy(bands)
    path = F.viterbi_track(P, 200)
    c = F.path_confidence(P, path)
    assert set(c) == {"median", "min", "weak_frac"}
    assert 0.0 <= c["weak_frac"] <= 1.0
    assert 0.0 <= c["min"] <= c["median"] <= 1.0
