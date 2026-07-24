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


def _goto_sync(tx, ty, skills=None):
    """同步導航到 (tx,ty),阻塞到完成。回是否到達。move_to 與巡邏循環共用。"""
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
    p = _dot()
    if p:
        _state["pos"] = list(p)
    arrived = bool(p and abs(p[0] - tx) <= X_TOL and abs(p[1] - ty) <= Y_TOL)
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


def _patrol_run(points, attack_key, cast_mode):
    """巡邏循環:巡邏點【順序完全隨機、但不連號】(不會連續造訪同一點,如 111)——
    例 1231 / 13231 / 1321231。每到一點:
      ① 平A攻擊(attack_key,長按2秒/按兩次)
      ② 若該點有設『放置技能』且冷卻已過 → 放一次(僅到點放、冷卻中略過)。"""
    pts = list(points)
    _place.clear()
    for p in pts:                          # 預先登記有放置技能的點(初始就緒),供監控下次觸發
        if p.get("skill"):
            _place[(p["x"], p["y"])] = {"skill": p["skill"],
                                        "cd": float(p.get("cd", 0) or 0), "last": None}
    visits = 0
    prev = None
    while not _stop.is_set():
        if len(pts) > 1:                   # 隨機挑下一點,排除上一點 → 不連號
            pt = random.choice([p for p in pts if p is not prev])
        else:
            pt = pts[0]
        prev = pt
        tx, ty = pt["x"], pt["y"]
        _state["phase"] = "goto"
        _goto_sync(tx, ty)
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
                _state["phase"] = "place"
                _release_move_keys()
                _press(sk, hold=0.1)
                rec["last"] = time.monotonic()
        # ③ 才離開(下方施放後延遲 0.5s,可中斷)
        visits += 1
        _state["rounds"] = visits          # 語意=已造訪點數(隨機無「圈」概念)
        if _stop.wait(0.5):                # 施放後延遲再移動(可中斷)
            return


def _patrol_wrap(points, attack_key, cast_mode):
    try:
        _state["error"] = ""
        if _focus_fn:
            try:
                _focus_fn()
            except Exception:
                pass
        _patrol_run(points, attack_key, cast_mode)
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


def patrol_start(points, attack_key="a", cast_mode="hold2s"):
    """啟動背景巡邏:隨機不連號造訪各點 + 到點平A、放置技能(含冷卻)。回 (ok,msg)。"""
    global _thread
    with _op_lock:
        if _state["running"]:
            return False, "導航/巡邏進行中"
        if _keyboard is None:
            return False, "鍵盤未連線"
        if not points:
            return False, "沒有巡邏點"
        _stop.clear()
        _state.update({"running": True, "phase": "patrol", "mode": "patrol",
                       "arrived": False, "error": "", "rounds": 0})
        _thread = threading.Thread(target=_patrol_wrap,
                                   args=(list(points), attack_key, cast_mode), daemon=True)
        _thread.start()
        return True, "ok"


def stop():
    _stop.set()
    t = _thread
    if t and t is not threading.current_thread():
        t.join(timeout=3.0)
    _state["running"] = False
    return True
