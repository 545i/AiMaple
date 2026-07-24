# -*- coding: utf-8 -*-
"""掛機自動導航:用移動向量庫(繩索/下跳/二段跳)閉環走到目標座標。

  * 背景執行緒跑,狀態可查詢(/nav/status),可中途停止。
  * 閉環:每步重讀小地圖黃點、算誤差、沒到就再動;卡住(位置沒變)自癒——
    強制轉向 + 走路脫困,避免二段跳撞牆空轉。
  * 移動時可同時按技能鍵(skills):二段跳/走路的同時按壓,不影響移動——
    這是本角色「移動中施放」的機制(技能不能單獨定點放)。
  * 僅掛機用;人不干涉。座標系=小地圖黃點 (x,y)。

向量參數來自校準(docs/jump-vectors.md):二段跳水平 ~30px、繩索上第4層、
下跳閉環。所有按鍵用 press(按住 60ms)避免 tap 漏讀。
"""
import random
import threading
import time

import minimap

_keyboard = None
_focus_fn = None
_thread = None
_stop = threading.Event()
_op_lock = threading.Lock()
_state = {"running": False, "phase": "idle", "target": None,
          "pos": None, "arrived": False, "error": "", "steps": 0}
_place = {}   # (x,y) → {"skill","cd","last"(monotonic|None)};放置技能冷卻狀態,供監控下次觸發

# ---- 向量/容差參數 ----
JUMP_DX = 30          # 二段跳一次水平飛距(px)
X_TOL = 3             # 水平到達容差
Y_TOL = 4             # 垂直到達容差
JUMP_INTERVAL = 0.20  # X→P 的間隔(down-to-down)
KEY_HOLD = 0.06
FALL_MAX = 8          # 下跳閉環最多跳幾次
PRECISE_X_TOL = 1     # 精確模式水平容差(px):二段跳到位後(可過衝)再走路回正到 ±1
PRECISE_STEPS = 10    # 精確走路回正最多步數
X_MAX_STEPS = 14      # 水平閉環步數上限


def set_keyboard(kb):
    global _keyboard
    _keyboard = kb


def set_focus_fn(fn):
    global _focus_fn
    _focus_fn = fn


def is_running():
    return _state["running"]


def status():
    s = dict(_state)
    now = time.monotonic()
    pl = []
    for (x, y), v in _place.items():
        last = v.get("last")
        rem = 0.0 if last is None else max(0.0, v["cd"] - (now - last))
        pl.append({"x": x, "y": y, "skill": v["skill"], "cd": v["cd"],
                   "remaining": round(rem, 1), "ready": rem <= 0.05})
    s["placements"] = pl
    return s


# ---------- 底層動作 ----------
def _dot():
    """讀一次小地圖黃點 (x,y)。抓不到回 None。"""
    try:
        minimap.detect_once()
        s = minimap.status()
        d = s.get("dot")
        if d and s.get("found") and not s.get("dot_stale"):
            return (d["x"], d["y"])
    except Exception:
        pass
    return None


def _settle(retries=8):
    """連續兩次位置接近 → 視為靜止,回位置。"""
    last = None
    for _ in range(retries):
        if _stop.is_set():
            return None
        p = _dot()
        if p is None:
            time.sleep(0.1)
            continue
        if last is not None and abs(p[0] - last[0]) <= 1 and abs(p[1] - last[1]) <= 1:
            return p
        last = p
        time.sleep(0.12)
    return last


def _press(k, hold=KEY_HOLD):
    _keyboard.key_down(k)
    time.sleep(hold)
    _keyboard.key_up(k)


def _release_move_keys():
    """放開所有移動鍵,確保平A/放置技能施放時角色靜止、只按該技能鍵。"""
    for k in ("left", "right", "up", "down"):
        try:
            _keyboard.key_up(k)
        except Exception:
            pass


def _same_skills(skills):
    """移動時同時按壓技能鍵(不影響移動)。tap 即可,不阻塞。"""
    if skills:
        for sk in skills:
            try:
                _keyboard.tap(sk)
            except Exception:
                pass


def _double_jump(direction, skills=None):
    """朝 direction 二段跳(X→interval→P),過程中同時按技能鍵。"""
    _press(direction)                      # 轉向(press,確保面向)
    time.sleep(0.12)
    _keyboard.key_down("x"); time.sleep(KEY_HOLD); _keyboard.key_up("x")
    time.sleep(max(0.0, JUMP_INTERVAL - KEY_HOLD))
    _keyboard.key_down("p"); time.sleep(KEY_HOLD); _keyboard.key_up("p")
    _same_skills(skills)                   # 移動中施放技能
    time.sleep(1.0)                        # 等落地


def _walk_to_x(direction, target_x, skills=None, timeout_s=2.5):
    """按住方向鍵走到 target_x(±X_TOL),過程中同時按技能鍵。撞牆/逾時停。"""
    _keyboard.key_down(direction)
    t0 = time.monotonic(); last = None; stuck = 0
    try:
        while time.monotonic() - t0 < timeout_s and not _stop.is_set():
            p = _dot()
            if p:
                _state["pos"] = list(p)
                reached = (direction == "right" and p[0] >= target_x - X_TOL) or \
                          (direction == "left" and p[0] <= target_x + X_TOL)
                if reached:
                    break
                if time.monotonic() - t0 > 0.8:
                    if last is not None and abs(p[0] - last) <= 1:
                        stuck += 1
                        if stuck >= 5:
                            break
                    else:
                        stuck = 0
                last = p[0]
            _same_skills(skills)
            time.sleep(0.04)
    finally:
        _keyboard.key_up(direction)
    time.sleep(0.2)


def _fall_to_y(ty, skills=None):
    """閉環下跳到目標 y:壓 down + 重複跳,讀黃點收斂,FALL_MAX 上限。"""
    _keyboard.key_down("down")
    time.sleep(0.2)
    try:
        for _ in range(FALL_MAX):
            if _stop.is_set():
                break
            p = _dot()
            if p:
                _state["pos"] = list(p)
                if p[1] >= ty - Y_TOL:
                    break
            _keyboard.key_down("x"); time.sleep(0.08); _keyboard.key_up("x")
            _same_skills(skills)
            time.sleep(0.45)
    finally:
        _keyboard.key_up("down")
    time.sleep(0.3)


def _rope_up(skills=None):
    """繩索 C 上升到第 4 層(不中斷)。"""
    _press("c")
    time.sleep(1.6)


# ---------- 導航主邏輯 ----------
def _move_to_x(tx, skills=None):
    """水平閉環:遠用二段跳、近用走路;卡住(x 沒變)自癒——換走路脫困。"""
    last_x = None
    for i in range(X_MAX_STEPS):
        if _stop.is_set():
            return
        p = _dot()
        if not p:
            continue
        _state["pos"] = list(p); _state["steps"] += 1
        dx = tx - p[0]
        if abs(dx) <= X_TOL:
            return
        d = "right" if dx > 0 else "left"
        # 卡住自癒:上一步後 x 幾乎沒變 → 二段跳撞牆/無效,改走路脫困
        stuck = last_x is not None and abs(p[0] - last_x) <= 1
        last_x = p[0]
        if abs(dx) > JUMP_DX - 5 and not stuck:
            _double_jump(d, skills)
        else:
            _walk_to_x(d, tx, skills)


def _fine_tune_x(tx, tol=PRECISE_X_TOL):
    """精確回正:二段跳到位後可能過衝,用短促走路脈衝雙向微調,收斂到 |dx|<=tol。
    距離越近脈衝越短、避免再次過衝(此處不用二段跳,那會又飛 ~30px)。"""
    for _ in range(PRECISE_STEPS):
        if _stop.is_set():
            return False
        p = _dot()
        if not p:
            continue
        _state["pos"] = list(p)
        dx = tx - p[0]
        if abs(dx) <= tol:
            return True
        d = "right" if dx > 0 else "left"
        dur = min(0.13, 0.03 + abs(dx) * 0.02)   # 距離越近走越短,避免過衝
        _keyboard.key_down(d)
        time.sleep(dur)
        _keyboard.key_up(d)
        time.sleep(0.18)                          # 等停穩再讀黃點
    return False


def _goto_sync(tx, ty, skills=None, precise=False):
    """同步導航到 (tx,ty),阻塞到完成。回是否到達。move_to 與巡邏循環共用。
    precise=True:水平照用二段跳/走路到容差內(可過衝),再走路回正到 ±PRECISE_X_TOL。"""
    _state.update({"target": [tx, ty], "arrived": False})
    p = _settle()
    if p is None:
        _state["error"] = "抓不到角色黃點"
        return False
    _state["pos"] = list(p)
    dy = ty - p[1]
    if dy < -Y_TOL:                        # 目標更高 → 繩索上
        _state["phase"] = "rope_up"
        _rope_up(skills)
        p = _dot() or p
        if p[1] > ty + Y_TOL:             # 繩索衝過頭 → 下跳修正
            _state["phase"] = "fall_adjust"
            _fall_to_y(ty, skills)
    elif dy > Y_TOL:                       # 目標更低 → 下跳
        _state["phase"] = "fall"
        _fall_to_y(ty, skills)
    _state["phase"] = "move_x"
    _move_to_x(tx, skills)
    if precise:                            # 精確到位:二段跳可能過衝 → 走路回正到 ±1px
        _state["phase"] = "fine_x"
        _fine_tune_x(tx)
    p = _dot()
    if p:
        _state["pos"] = list(p)
    xtol = PRECISE_X_TOL if precise else X_TOL
    arrived = bool(p and abs(p[0] - tx) <= xtol and abs(p[1] - ty) <= Y_TOL)
    _state["arrived"] = arrived
    return arrived


def _run(tx, ty, skills):
    try:
        _state.update({"phase": "settle", "steps": 0, "error": ""})
        if _focus_fn:
            try:
                _focus_fn()
            except Exception:
                pass
        _goto_sync(tx, ty, skills)
        _state["phase"] = "done"
    except Exception as e:
        _state["error"] = f"{e!r}"
        print(f"[nav] 導航錯誤: {e!r}")
    finally:
        for k in ("left", "right", "down"):
            try:
                _keyboard.key_up(k)
            except Exception:
                pass
        _state["running"] = False


def _cast_skill(skill_key, mode="hold2s"):
    """到站立點施放攻擊技能。mode: hold2s(長按 2 秒) / tap2(按兩次)。"""
    try:
        if mode == "tap2":
            _press(skill_key)
            time.sleep(0.3)
            _press(skill_key)
        else:
            _keyboard.key_down(skill_key)
            time.sleep(2.0)
            _keyboard.key_up(skill_key)
    except Exception:
        pass


def _refresh_places(pts):
    """依最新點清單更新放置技能冷卻表(保留同鍵既有冷卻,故運行中改設定即時生效、
    冷卻計時不歸零)。"""
    new = {}
    for p in pts:
        sk = p.get("skill")
        if sk:
            key = (p["x"], p["y"])
            old = _place.get(key)
            new[key] = {"skill": sk, "cd": float(p.get("cd", 0) or 0),
                        "last": old["last"] if (old and old["skill"] == sk) else None}
    _place.clear()
    _place.update(new)


def _purple_present():
    """小地圖目前是否有紫標。抓不到狀態回 False。"""
    try:
        return bool(minimap.status().get("purple"))
    except Exception:
        return False


def _patrol_run(points_fn, attack_key, cast_mode):
    """巡邏循環:【每輪重讀最新巡邏點】→ 運行中新增/修改放置技能、冷卻即時生效。
    巡邏點順序完全隨機、不連號(不會連續造訪同一點,如 111;例 1231/13231/1321231)。
    到點:① 先平A(確保落地站穩) ② 放置技能(冷卻已過才放,僅到點、只按該鍵、不移動)。
    紫標(特殊NPC/玩家進圖)出現 → 自動暫停巡邏(危險規避;另有即時 hook 兜兩道)。
    點若設 precise=True,到點會走路回正到 ±1px 才施放(定點放置技能)。"""
    visits = 0
    prev = None                            # 上一點座標 (x,y),用於不連號
    while not _stop.is_set():
        if _purple_present():              # 紫標兜底:巡邏中/重開時紫標仍在 → 暫停
            _state["phase"] = "purple_pause"
            _state["error"] = "偵測到紫標,已自動暫停巡邏"
            print("[nav] 紫標存在 → 暫停巡邏")
            return
        pts = points_fn() or []            # 每輪重讀:運行中改放置技能/冷卻即時生效
        if not pts:
            if _stop.wait(0.5):
                return
            continue
        _refresh_places(pts)
        choices = [p for p in pts if (p["x"], p["y"]) != prev]   # 排除上一點座標→不連號
        pt = random.choice(choices) if choices else pts[0]
        prev = (pt["x"], pt["y"])
        tx, ty = pt["x"], pt["y"]
        _state["phase"] = "goto"
        _goto_sync(tx, ty, precise=bool(pt.get("precise")))
        if _stop.is_set():
            return
        _release_move_keys()               # 到點靜止:先放開移動鍵,平A/放置技能時皆不移動
        # ① 先平A:先攻擊可確保角色已落地站穩(避免還在下落時放置技能)
        _state["phase"] = "cast"
        _cast_skill(attack_key, cast_mode)
        # ② 再檢查放置技能:冷卻已過則放置(僅到點、只按該鍵、絕不移動)
        sk = pt.get("skill")
        if sk:
            rec = _place.get((tx, ty))
            now = time.monotonic()
            if rec and (rec["last"] is None or now - rec["last"] >= rec["cd"]):
                # 安全停止攻擊:放開平A鍵與移動鍵 + 等攻擊後搖結束、角色站穩,
                # 再放置(否則角色還在攻擊動作中,放置技能常被吃掉→成功率低)
                try:
                    _keyboard.key_up(attack_key)
                except Exception:
                    pass
                _release_move_keys()
                if _stop.wait(0.45):       # 後搖緩衝(可中斷)
                    return
                _state["phase"] = "place"
                _press(sk, hold=0.12)
                if _stop.wait(0.2):        # 放置後稍等,確保技能發動再離開
                    return
                rec["last"] = time.monotonic()
        # ③ 才離開(下方施放後延遲 0.5s,可中斷)
        visits += 1
        _state["rounds"] = visits          # 語意=已造訪點數(隨機無「圈」概念)
        if _stop.wait(0.5):                # 施放後延遲再移動(可中斷)
            return


def _patrol_wrap(points_fn, attack_key, cast_mode):
    try:
        _state["error"] = ""
        if _focus_fn:
            try:
                _focus_fn()
            except Exception:
                pass
        _patrol_run(points_fn, attack_key, cast_mode)
    except Exception as e:
        _state["error"] = f"{e!r}"
        print(f"[nav] 巡邏錯誤: {e!r}")
    finally:
        for k in ("left", "right", "down"):
            try:
                _keyboard.key_up(k)
            except Exception:
                pass
        _state["running"] = False


def move_to(tx, ty, skills=None):
    """啟動背景導航到 (tx,ty)。skills=移動時同時按的技能鍵列表。回 (ok,msg)。"""
    global _thread
    with _op_lock:
        if _state["running"]:
            return False, "導航進行中"
        if _keyboard is None:
            return False, "鍵盤未連線"
        _stop.clear()
        _state.update({"running": True, "phase": "starting", "target": [tx, ty],
                       "arrived": False, "error": ""})
        _thread = threading.Thread(target=_run, args=(int(tx), int(ty), skills or []),
                                   daemon=True)
        _thread.start()
        return True, "ok"


def patrol_start(points_fn, attack_key="a", cast_mode="hold2s"):
    """啟動背景巡邏。points_fn=無參函式,回傳最新巡邏點清單;每輪重讀 →
    巡邏運行中新增/修改放置技能、冷卻皆即時生效(不必停巡邏)。回 (ok,msg)。"""
    global _thread
    with _op_lock:
        if _state["running"]:
            return False, "導航/巡邏進行中"
        if _keyboard is None:
            return False, "鍵盤未連線"
        if not (points_fn() or []):
            return False, "沒有巡邏點"
        _stop.clear()
        _place.clear()
        _state.update({"running": True, "phase": "patrol", "mode": "patrol",
                       "arrived": False, "error": "", "rounds": 0})
        _thread = threading.Thread(target=_patrol_wrap,
                                   args=(points_fn, attack_key, cast_mode), daemon=True)
        _thread.start()
        return True, "ok"


def stop():
    _stop.set()
    t = _thread
    if t and t is not threading.current_thread():
        t.join(timeout=3.0)
    _state["running"] = False
    return True


def pause_purple():
    """紫標偵測 hook 呼叫:標記原因後停止巡邏/導航(危險規避)。
    若由巡邏執行緒自身觸發(_dot→detect→hook),stop() 只 set 事件、不 join 自己→安全。"""
    if _state.get("running"):
        _state["error"] = "偵測到紫標,已自動暫停巡邏"
        _state["phase"] = "purple_pause"
        print("[nav] 紫標 hook → 暫停巡邏")
    stop()
