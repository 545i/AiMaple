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


def build_from_platforms(points, platforms, ropes, y_tol=4, margin=1):
    """用【平台(可走線段)+繩索(x)】幾何建圖,免探測點對可走性:
      * 平台 = {"y":層, "xA":左端, "xB":右端};繩索 = {"x":位置}。
      * 節點 = 記錄點 + 繩索在【每個穿過的平台】的進出點(繩索 x 落在 [xA,xB] 內)。
      * 邊:①同一平台內任兩節點 walk ②繩索連相鄰上下平台的進出點 rope(上)/fall(下)。
    繩索覆蓋哪些層完全由幾何推得(繩索 x 落在哪些平台區間),不需探測。回 (nodes, edges)。"""
    def plat_of(x, y):
        for i, pf in enumerate(platforms):
            if abs(pf["y"] - y) <= y_tol and pf["xA"] - margin <= x <= pf["xB"] + margin:
                return i
        return None

    nodes = [(int(x), int(y)) for x, y in points]
    node_plat = {n: plat_of(n[0], n[1]) for n in nodes}
    rope_pts = {}                                   # rope_idx -> [(node, y), ...] 依 y
    for ri, r in enumerate(ropes):
        rx = int(r["x"]); pts = []
        for pf in platforms:
            if pf["xA"] - margin <= rx <= pf["xB"] + margin:
                n = (rx, int(pf["y"]))
                if n not in nodes:
                    nodes.append(n)
                node_plat[n] = platforms.index(pf)
                pts.append((n, int(pf["y"])))
        rope_pts[ri] = sorted(pts, key=lambda t: t[1])   # y 小(高層)在前
    edges = {n: [] for n in nodes}
    # ① 同平台互走
    for a in nodes:
        pa = node_plat.get(a)
        if pa is None:
            continue
        for b in nodes:
            if a != b and node_plat.get(b) == pa:
                edges[a].append((b, abs(a[0] - b[0]) + 1, "walk"))
    # ② 繩索相鄰層上下
    for pts in rope_pts.values():
        for i in range(len(pts) - 1):
            up, dn = pts[i][0], pts[i + 1][0]           # up 高(y小), dn 低(y大)
            dy = abs(dn[1] - up[1]) + 1
            edges[dn].append((up, dy, "rope"))          # 下→上:上繩
            edges[up].append((dn, dy, "fall"))          # 上→下:下跳
    return nodes, edges


def build_overlap(points, platforms, jump_dy=11, y_tol=4, overlap_min=2):
    """【只用平台】建圖:層間連接由平台 x 重疊幾何推得,不需任何繩索資料。
      * 兩平台 x 重疊 → 重疊區可上下;連接點取重疊區中點(節點)。
      * 高度差 <= jump_dy → 上升動作 "jump"(二段跳,重疊區任意點可上,零繩索);
        否則 "rope"(需繩索,確切 x 執行時在此重疊區探測/緩存)。
      * 只連相鄰層(兩平台間該重疊 x 無第三平台夾在中間),避免跨層直連。
    回 (nodes, edges);邊型:walk/jump(上)/rope(上)/fall(下,下跳)。"""
    plats = [dict(p) for p in platforms]
    nodes = [(int(x), int(y)) for x, y in points]
    node_plat = {}
    for n in nodes:
        for i, pf in enumerate(plats):
            if abs(pf["y"] - n[1]) <= y_tol and pf["xA"] - 1 <= n[0] <= pf["xB"] + 1:
                node_plat[n] = i
                break
    conns = []                                          # (nlo, nhi, dy, mode)
    for i in range(len(plats)):
        for j in range(len(plats)):
            a, b = plats[i], plats[j]
            if a["y"] <= b["y"]:                        # 只處理 a 低(y大) → b 高(y小)
                continue
            ox1, ox2 = max(a["xA"], b["xA"]), min(a["xB"], b["xB"])
            if ox2 - ox1 < overlap_min:
                continue
            # 相鄰層檢查:a、b 之間該重疊 x 範圍內無第三平台夾層
            mid = (ox1 + ox2) // 2
            blocked = any(b["y"] < c["y"] < a["y"] and c["xA"] - 1 <= mid <= c["xB"] + 1
                          for c in plats)
            if blocked:
                continue
            dy = a["y"] - b["y"]
            nlo, nhi = (mid, a["y"]), (mid, b["y"])
            for n, pi in ((nlo, i), (nhi, j)):
                if n not in nodes:
                    nodes.append(n)
                    node_plat[n] = pi
            conns.append((nlo, nhi, dy, "jump" if dy <= jump_dy else "rope"))
    edges = {n: [] for n in nodes}
    for a in nodes:                                     # 同平台 walk
        pa = node_plat.get(a)
        if pa is None:
            continue
        for b in nodes:
            if a != b and node_plat.get(b) == pa:
                edges[a].append((b, abs(a[0] - b[0]) + 1, "walk"))
    for nlo, nhi, dy, mode in conns:                    # 層間:上(jump/rope)、下(fall)
        edges[nlo].append((nhi, dy + 1, mode))
        edges[nhi].append((nlo, dy + 1, "fall"))
    return nodes, edges
