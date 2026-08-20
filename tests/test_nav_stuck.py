# -*- coding: utf-8 -*-
"""角色卡在「沒有節點的層」時不能空轉重規劃 —— 要先脫困歸位。

【實測卡死案例(2026-08-20,軌跡 seq 記錄)】
    角色 (18,51),目標 (26,45),地圖平台 y=34/45/56/53/53/29/40/54/68
    events: replan ×3(y=51 應為 45) → replan_exhausted → 停在原地不動
兩個 bug 疊在一起:
  1. nearest_node 只把 y 差加權 3 倍,沒有真的「同層優先」,於是選了跨層的 (26,45)
     ——而執行端的第一個檢查就是「現在的層是不是起點那一層」,跨層必定立刻失敗,
     重規劃又選到同一個節點,三次之後放棄。
  2. _on_some_platform 只看 y 不看 x:地圖有 y=53 的平台(x 44~62、85~124),
     角色在 x=18 根本不在上面,卻被判定「在層上」,脫困因此從來沒有觸發。
"""
import pathgraph


# ---------- nearest_node:同層一律優先 ----------
def test_nearest_node_prefers_same_layer_even_if_farther():
    """這就是卡死案例的數字:跨層的 (26,45) 加權距離 26,同層的 (44,53) 是 32 ——
    加權比不過,但它才是唯一到得了的起點。"""
    nodes = [(26, 45), (44, 53), (100, 68)]
    assert pathgraph.nearest_node(nodes, (18, 51)) == (44, 53)


def test_nearest_node_falls_back_when_no_node_on_this_layer():
    """該層沒有任何節點時仍要回一個(呼叫端會據此判定要先脫困),不能回 None。"""
    nodes = [(26, 45), (100, 68)]
    assert pathgraph.nearest_node(nodes, (18, 51)) == (26, 45)


def test_nearest_node_same_layer_picks_the_closest_one():
    nodes = [(10, 45), (30, 45), (100, 68)]
    assert pathgraph.nearest_node(nodes, (28, 45)) == (30, 45)


def test_nearest_node_layer_tolerance_is_inclusive():
    """剛好差 tol 也要算同層。差一格就把人推去「跨層」那條路,代價是多一次脫困。
    (10,45) 相對 y=49 差 4 = 容差邊界,要進同層池;(60,60) 差 11,不算。"""
    nodes = [(10, 45), (60, 60)]
    assert pathgraph.nearest_node(nodes, (12, 49), tol=4) == (10, 45)


# ---------- _on_some_platform:x 也要算 ----------
def test_on_some_platform_requires_x_inside_the_platform():
    import navigator
    plats = [{"y": 53, "xA": 44, "xB": 62}, {"y": 53, "xA": 85, "xB": 124}]
    # 卡死案例:y 對得上(51 vs 53),但 x=18 不在任何一塊上
    assert navigator._on_some_platform((18, 51), plats) is False
    assert navigator._on_some_platform((50, 51), plats) is True


def test_on_some_platform_allows_a_small_margin_at_the_edges():
    """站在平台邊緣時黃點可能差一兩格,不能因此判定「不在層上」而誤觸發脫困。"""
    import navigator
    plats = [{"y": 68, "xA": 10, "xB": 20}]
    assert navigator._on_some_platform((9, 68), plats) is True
    assert navigator._on_some_platform((22, 68), plats) is True
    assert navigator._on_some_platform((30, 68), plats) is False


def test_on_some_platform_rejects_wrong_layer():
    import navigator
    plats = [{"y": 68, "xA": 0, "xB": 160}]
    assert navigator._on_some_platform((80, 68), plats) is True
    assert navigator._on_some_platform((80, 51), plats) is False


def test_on_some_platform_handles_missing_position():
    import navigator
    assert navigator._on_some_platform(None, [{"y": 1, "xA": 0, "xB": 9}]) is False
