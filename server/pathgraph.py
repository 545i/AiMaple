# -*- coding: utf-8 -*-
"""記錄點連接圖(輕量拓撲圖):用【記錄點 + 繩索延伸節點】做跨層路徑規劃。

設計理念(依使用者洞察):
  * 記錄點本身就是「另類地形圖」——是使用者手動確認能站、要去的節點。
  * 不做像素級地形/繩索的【視覺識別】(會被小地圖上其他玩家的彩色點干擾),
    改用【行為探測】:只看可靠的角色黃點——走到某 x 按 C 看黃點 y 有沒有上升
    (找繩索)、實際試走點對看黃點到不到(建邊)。玩家點完全不影響。
  * 繩索是層間「電梯」:把繩索在【每一層的進出點】當成延伸節點納入圖,路徑計算
    就能自動經繩索跨層(繩索不必在目標點正下方)。

節點 = (x, y) tuple。邊帶動作類型:
  "walk"(同層走)、"rope"(上繩到相鄰上層)、"fall"(下跳/下繩到相鄰下層)。
本模組只管【圖結構與最短路徑】(純算法、不動角色);繩索/邊的探測由 navigator 做。
"""
import heapq


def build(points, ropes, y_tol=4):
    """建圖。points:[(x,y),...] 記錄點;ropes:[{"x":rx,"levels":[y1,y2,...]}] 繩索
    (每條繩索覆蓋的層 y 由探測得出)。回 (nodes, edges)。
    邊:①同層(|dy|<=y_tol)節點互走 walk ②繩索相鄰層 rope(上)/fall(下)。
    註:同層 walk 邊此處先全連(假設同層可達);實際可走性由探測覆蓋(見 set_walkable)。"""
    nodes = [(int(x), int(y)) for x, y in points]
    for r in ropes:
        for y in r["levels"]:
            n = (int(r["x"]), int(y))
            if n not in nodes:
                nodes.append(n)
    edges = {n: [] for n in nodes}
    # ① 同層互走
    for a in nodes:
        for b in nodes:
            if a is b or a == b:
                continue
            if abs(a[1] - b[1]) <= y_tol:
                edges[a].append((b, abs(a[0] - b[0]) + 1, "walk"))
    # ② 繩索上下(同 x 相鄰層)
    for r in ropes:
        rx = int(r["x"])
        lv = sorted(int(y) for y in r["levels"])
        for i in range(len(lv) - 1):
            lo, hi = (rx, lv[i + 1]), (rx, lv[i])   # lv 由小到大;y 小=高層
            # hi(y小,高) 在上, lo(y大,低) 在下
            up, dn = (rx, lv[i]), (rx, lv[i + 1])
            edges[dn].append((up, (lv[i + 1] - lv[i]) + 1, "rope"))   # 下→上:上繩
            edges[up].append((dn, (lv[i + 1] - lv[i]) + 1, "fall"))   # 上→下:下跳
    return nodes, edges


def nearest_node(nodes, pos, tol=4):
    """把當前黃點位置對應到最近的圖節點(同層優先);超出 tol 回最近點。"""
    best, bd = None, 1e9
    for n in nodes:
        d = abs(n[0] - pos[0]) + abs(n[1] - pos[1]) * 3   # y 差權重高(層更重要)
        if d < bd:
            best, bd = n, d
    return best


def shortest_path(edges, start, goal):
    """Dijkstra。回 [(node, move_type), ...]:依序要【到達每個節點的動作】。
    無路徑回 None。start==goal 回 []。"""
    if start == goal:
        return []
    dist = {start: 0}
    prev = {}
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            break
        if d > dist.get(u, 1e18):
            continue
        for v, cost, mt in edges.get(u, []):
            nd = d + cost
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, mt)
                heapq.heappush(pq, (nd, v))
    if goal not in dist:
        return None
    path = []
    cur = goal
    while cur != start:
        u, mt = prev[cur]
        path.append((cur, mt))
        cur = u
    path.reverse()
    return path
