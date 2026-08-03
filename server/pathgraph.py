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


def build_overlap(points, platforms, jump_dy=11, jump_dx=30, y_tol=4):
    """【只用平台+跳躍數據】建圖:平台間連接由 x 重疊/接近 + 高度差幾何推得。
      * x 重疊 → 上下連接(重疊中點);x 接近(間隙<=jump_dx,二段跳水平距離)→ 橫向
        跨間隙連接(相近端點) → 避免為了橫移而繞到最低層。
      * 依高度差(a→b)選動作:更低→fall(下跳,只在目標更低時、連相鄰平台不下過頭);
        更高且<=jump_dy→jump(二段跳);更高且大落差(垂直重疊)→rope(繩索);同層→walk。
    cost=水平+垂直距離(Dijkstra 取最短)。回 (nodes, edges);邊型 walk/jump/rope/fall。"""
    plats = [dict(p) for p in platforms]
    nodes = [(int(x), int(y)) for x, y in points]
    node_plat = {}
    for n in nodes:
        for i, pf in enumerate(plats):
            if abs(pf["y"] - n[1]) <= y_tol and pf["xA"] - 1 <= n[0] <= pf["xB"] + 1:
                node_plat[n] = i
                break

    def add_node(n, pi):
        if n not in nodes:
            nodes.append(n)
            node_plat[n] = pi

    conns = []                                          # (na, nb, cost, mode)  a→b
    for i in range(len(plats)):
        for j in range(len(plats)):
            if i == j:
                continue
            a, b = plats[i], plats[j]
            dy = b["y"] - a["y"]                         # 正=b 更低
            ox1, ox2 = max(a["xA"], b["xA"]), min(a["xB"], b["xB"])
            if ox2 >= ox1:                              # x 重疊 → 上下連接(中點)
                cx = (ox1 + ox2) // 2
                # 相鄰層檢查:中間無第三平台夾層
                if any(min(a["y"], b["y"]) < c["y"] < max(a["y"], b["y"])
                       and c["xA"] - 1 <= cx <= c["xB"] + 1 for c in plats):
                    continue
                na, nb, gap = (cx, a["y"]), (cx, b["y"]), 0
            else:                                       # x 不重疊 → 看間隙
                gap = ox1 - ox2
                if gap > jump_dx:                       # 太遠,跨不過
                    continue
                if b["xA"] > a["xB"]:                   # b 在 a 右 → 各取相近端點
                    na, nb = (a["xB"], a["y"]), (b["xA"], b["y"])
                else:                                   # b 在 a 左
                    na, nb = (a["xA"], a["y"]), (b["xB"], b["y"])
            if dy > y_tol:                              # b 更低 → 下跳
                mode = "fall"
            elif dy < -y_tol:                           # b 更高 → 上升
                if -dy <= jump_dy:
                    mode = "jump"                       # 小落差二段跳
                elif gap == 0:
                    mode = "rope"                       # 大落差且垂直重疊 → 繩索
                else:
                    continue                            # 高又有間隙,跳不上
            else:
                mode = "walk" if gap == 0 else "jump"   # 同層:重疊走、有間隙小跳
            add_node(na, i)
            add_node(nb, j)
            conns.append((na, nb, abs(na[0] - nb[0]) + abs(dy) + 1, mode))
    edges = {n: [] for n in nodes}
    for a in nodes:                                     # 同平台 walk
        pa = node_plat.get(a)
        if pa is None:
            continue
        for b in nodes:
            if a != b and node_plat.get(b) == pa:
                edges[a].append((b, abs(a[0] - b[0]) + 1, "walk"))
    for na, nb, cost, mode in conns:
        edges[na].append((nb, cost, mode))
    return nodes, edges


def build_physics(points, platforms, jump=30, jump_up=8, y_tol=4,
                  free_vertical=False, blink_dy=22, blink_up=10):
    """完整移動模型建圖(用實測落點):
      * walk:同平台。
      * jump:二段跳——從平台端點/中點朝左右飛約 jump(30)px,落到落點 x 處的平台
        (可略升<=jump_up、可大降;抛物線落點,實測同層 30px)。
        jump_up 原本是 11,由 nav_moves.jsonl 7892 筆實測修正為 8:
          上升  8 格 404/404 成功(100%)
          上升 11 格   1/8  成功(12.5%)  ← 11 其實跳不上去
          上升 15/23 格 全失敗
        設 11 會讓「差 11 格的兩層」被規劃成二段跳而一路失敗(實測有地圖 y=51→y=40
        正好差 11,導航反覆嘗試都上不去,得靠繩索)。
      * fall:掉落——a 的 x 正下方有平台(中間無夾層)→ 垂直落下。
      * rope:大落差上升——a 上方有重疊平台且升幅>jump_up → 走到重疊區上繩到頂。
    每種邊都對應一個可靠的按鍵動作。回 (nodes, edges)。

    free_vertical=True 給【瞬移職業】(陰陽師)用,規則有兩處不同:
      * jump 邊不再允許略升。瞬移的水平位移實測 dy 恆為 0,沒有垂直分量,
        靠它「跳上」略高的平台是二段跳才有的性質,照用會規劃出上不去的路徑。
      * 垂直邊不限升幅。瞬移在任意 x 都能直接上下到【相鄰】平台,不需要繩索,
        所以連 jump_up 以內的小升幅也走這種邊(二段跳職業那些是由 jump 邊涵蓋的)。
        夾層檢查(blocked)照舊 —— 瞬移只會停在最近那層,中間有平台就到不了目標。"""
    plats = [dict(p) for p in platforms]
    nodes = [(int(x), int(y)) for x, y in points]
    node_plat = {}
    for n in nodes:
        for i, pf in enumerate(plats):
            if abs(pf["y"] - n[1]) <= y_tol and pf["xA"] - 1 <= n[0] <= pf["xB"] + 1:
                node_plat[n] = i
                break

    def addn(n, pi):
        if n not in nodes:
            nodes.append(n)
            node_plat[n] = pi

    def blocked(cx, y1, y2):                             # cx 處 y1~y2 之間有夾層平台?
        return any(min(y1, y2) < c["y"] < max(y1, y2) and c["xA"] - 1 <= cx <= c["xB"] + 1
                   for c in plats)

    conns = []
    for i, a in enumerate(plats):
        amid = (a["xA"] + a["xB"]) // 2
        # 二段跳:從 a 端點/中點朝左右飛 jump,落到落點平台(略升 or 下降)
        for jx in (a["xA"], a["xB"], amid):
            for dr in (1, -1):
                lx = jx + dr * jump
                # 【水平瞬移會跨層】它落到「水平距離處的平台」,高度差在 blink_up 內
                # 都接得住(實測差 10 的兩塊平台可直接互跳,6 次全成立)。
                # 先前誤以為瞬移純水平而設 0,結果路徑被迫「先垂直上去再水平」,繞遠路。
                up_allow = blink_up if free_vertical else jump_up
                for j, b in enumerate(plats):
                    if j == i or b["y"] < a["y"] - up_allow:   # 不能落到比 a 高太多的平台
                        continue
                    if b["xA"] - 3 <= lx <= b["xB"] + 3:
                        nb = (max(b["xA"], min(b["xB"], lx)), b["y"])
                        addn((jx, a["y"]), i); addn(nb, j)
                        conns.append(((jx, a["y"]), nb, jump + abs(b["y"] - a["y"]) + 1, "jump"))
        if free_vertical:
            # 【瞬移的垂直規則】它會【越過較近的平台】落到範圍內【最遠】那個,
            # 不是停在最近的。實測 x=98 從 y=49 往上,範圍內有 y=38(距 11)與
            # y=27(距 22),結果直接到 27 —— y=38 在那個 x 根本到不了。
            # 所以每個方向【只建一條邊】,連到最遠的可達平台;若照一般寫法把較近的
            # 也連上,導航就會在兩層之間無限來回(實測 90 秒只走到 6 個點)。
            # 也因此不做 blocked 檢查:夾層擋不住瞬移,反而是它被越過的原因。
            for sign in (-1, 1):             # -1=往上(y 變小)、+1=往下
                best = None
                for j, b in enumerate(plats):
                    if j == i:
                        continue
                    d = (b["y"] - a["y"]) * sign
                    if d <= 0 or d > blink_dy:
                        continue
                    ox1, ox2 = max(a["xA"], b["xA"]), min(a["xB"], b["xB"])
                    if ox2 < ox1:
                        continue
                    if best is None or d > best[0]:
                        best = (d, j, b, (ox1 + ox2) // 2)
                if best:
                    d, j, b, cx = best
                    addn((cx, a["y"]), i); addn((cx, b["y"]), j)
                    conns.append(((cx, a["y"]), (cx, b["y"]), d + 1,
                                  "fall" if sign > 0 else "rope"))
        else:
            # 掉落:a 的 x 正下方有平台
            for j, b in enumerate(plats):
                if j == i or b["y"] <= a["y"]:
                    continue
                ox1, ox2 = max(a["xA"], b["xA"]), min(a["xB"], b["xB"])
                if ox2 < ox1:
                    continue
                cx = (ox1 + ox2) // 2
                if blocked(cx, a["y"], b["y"]):
                    continue
                addn((cx, a["y"]), i); addn((cx, b["y"]), j)
                conns.append(((cx, a["y"]), (cx, b["y"]), abs(b["y"] - a["y"]) + 1, "fall"))
            # 上升:靠繩索,只在大落差(>jump_up)時建邊 —— 小落差由 jump 邊涵蓋。
            for j, b in enumerate(plats):
                if j == i or b["y"] >= a["y"] or a["y"] - b["y"] <= jump_up:
                    continue
                ox1, ox2 = max(a["xA"], b["xA"]), min(a["xB"], b["xB"])
                if ox2 < ox1:
                    continue
                cx = (ox1 + ox2) // 2
                if blocked(cx, a["y"], b["y"]):
                    continue
                addn((cx, a["y"]), i); addn((cx, b["y"]), j)
                conns.append(((cx, a["y"]), (cx, b["y"]), (a["y"] - b["y"]) + 1, "rope"))
    edges = {n: [] for n in nodes}
    for a in nodes:
        pa = node_plat.get(a)
        if pa is None:
            continue
        for b in nodes:
            if a != b and node_plat.get(b) == pa:
                edges[a].append((b, abs(a[0] - b[0]) + 1, "walk"))
    for na, nb, cost, mode in conns:
        edges[na].append((nb, cost, mode))
    return nodes, edges
