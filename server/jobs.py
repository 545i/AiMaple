# -*- coding: utf-8 -*-
"""職業設定:把「換一個角色就要重調」的參數收成一組,可具名保存與切換。

--------------------------------------------------------------------------
為什麼要有這一層
--------------------------------------------------------------------------
原本攻擊鍵存在 layouts/_attack.json,而移動參數(二段跳飛多遠、兩段之間隔多久、
跳躍/上繩用哪個鍵、等落地多久)全部【寫死在 navigator.py 裡】。那些數字是對著
一個角色量出來的 —— 二段跳水平 30px、上繩 1.6 秒,換個職業就全部不對:
位移距離不同、技能鍵不同、動作前後搖也不同。

所以把它們收攏成「職業」:一個職業 = 一組攻擊設定 + 一組移動參數。
切換職業時兩者一起套用,不必逐項重設。

--------------------------------------------------------------------------
與既有設定的關係
--------------------------------------------------------------------------
【刻意不動 layouts/_attack.json】它仍然是「當前生效的攻擊設定」,既有的 UI 與
/map/attack 端點照常運作。切換職業時把該職業的攻擊設定【寫進】它,所以:
  * 使用者臨時改攻擊鍵 → 只影響當前,不會污染職業設定
  * 想把臨時的調整存起來 → 呼叫 save(name) 把當前狀態存成職業
這樣「當前狀態」與「具名設定」分離,跟 profiles(地圖設置)是同一套思路。

地圖層級的東西(巡邏點、平台、繩索)不屬於職業 —— 那些跟地形綁定,是 profiles 管的。
"""
import json
import os
import threading

import paths

_DIR = paths.data_dir("jobs")
_CUR_PATH = os.path.join(_DIR, "_current.json")
_lock = threading.Lock()

# 預設值 = 目前寫在 navigator.py 裡的那組,量自職業「蓮」。
# 【這些數字的來源】見 DEV_LOG 的移動模型:二段跳同層水平精確 30px(16 組實測 ±1、
# 左右對稱);X→P 間隔 0.2s;繩索按 C 一路上到頂約 1.6s。換職業必須重新量。
DEFAULT_MOVE = {
    # 移動方式。不同職業的位移原語根本不同,不是換個鍵就好:
    #   double_jump 二段跳 + 繩索 + 下跳(蓮)
    #   blink       瞬移(陰陽師):水平固定距離、垂直直接到相鄰平台,不需要繩索
    "type": "double_jump",
    # --- double_jump 用 ---
    "jump_key1": "x",        # 二段跳第一段(也用於下跳、卡住脫困)
    "jump_key2": "p",        # 二段跳第二段
    "rope_key": "c",         # 上繩
    "jump_dx": 30,           # 二段跳一次的水平飛距(小地圖 px)
    "jump_interval": 0.20,   # 第一段到第二段的間隔(down-to-down)
    "jump_land": 1.0,        # 二段跳後等落地
    "rope_up": 1.6,          # 上繩到頂
    # --- blink 用 ---
    "blink_key": "v",        # 瞬移鍵
    "blink_dx": 20,          # 一次瞬移的【實際水平位移】,含 blink_wait 期間走的那段。
                             # 必須與時序參數配套:wait=0.60 時是 28、wait=0.20 時是 20。
                             # 建圖用它算跳躍邊,填大了會規劃出飛不到的路徑。
    "dir_hold": 0.08,        # 按 blink_key 之前方向鍵要先按住多久。一度以為要 0.15 ——
                             # 那是量測時遊戲沒有焦點造成的假象(按鍵全送進終端機)。
                             # 焦點正確後 0.06 就觸發,取 0.08 留餘裕。
    "blink_wait": 0.20,      # 瞬移後等動作結束。方向鍵在這段期間仍按著,所以它同時
                             # 決定「瞬移後又走了多遠」—— 壓短會讓單次位移變小,
                             # 但每秒移動距離反而變大(見 blink_dx 的說明)。
    "blink_tail": 0.08,      # 放開方向鍵後的緩衝
    "blink_dy_max": 22,      # 垂直瞬移一次最遠能跨多少層距。
                             # 【怎麼量出來的】用地圖幾何當尺,比逐點實測快:
                             #   y=49 → y=27(距 22) 直達      → 22 可達
                             #   y=49 → y=23(距 26) 必須經中間層 → 26 不可達
                             # 所以上界落在 [22,26),取已驗證的 22。
                             # 【關鍵性質】瞬移會【越過較近的平台】落到範圍內【最遠】
                             # 那個,不是停在最近的 —— 建圖規則整個依賴這一點。
}

# 主動技能的排程(目前只有召喚,之後要加別的再擴充這裡)
DEFAULT_SKILLS = {
    "summon_key": "",            # 空 = 此職業沒有召喚
    "summon_cd": 0.0,            # 冷卻秒數
    "summon_while_moving": True,  # 可否邊移動邊放
    "summon_priority": True,      # 冷卻一好就優先放(主要輸出來源)
    "summon_pause_on_rune": True,  # 解符文期間暫停放 —— 召喚物會遮住小地圖的符文紫標
}
DEFAULT_ATTACK = {"key": "a", "mode": "move", "jump_atk": True, "fall_atk": True}
DEFAULT_NAME = "蓮"


def _safe(name):
    """檔名安全化。與 mapdata._safe_name 同樣的理由:使用者可以輸入任意職業名。"""
    keep = "".join(c for c in str(name or "").strip()
                   if c.isalnum() or c in "._-()（）" or ord(c) > 127)
    return keep[:40]


def _path(name):
    return os.path.join(_DIR, _safe(name) + ".json")


def _norm_move(m):
    m = dict(m or {})
    out = dict(DEFAULT_MOVE)
    t = str(m.get("type", out["type"]) or "").strip().lower()
    out["type"] = t if t in ("double_jump", "blink") else DEFAULT_MOVE["type"]
    # 必要的鍵:空值沒有意義,退回預設
    for k in ("jump_key1", "blink_key"):
        v = str(m.get(k, out[k]) or "").strip().lower()[:12]
        out[k] = v or DEFAULT_MOVE[k]
    # 可選的鍵:空字串是有意義的 —— 代表【此職業沒有這個動作】。
    # 陰陽師沒有二段跳(實測單跳 18、連按 X,X 只有 21,差 3 是落地殘餘不是第二段),
    # 也不需要繩索(瞬移可直接上下層)。原本這裡一律 `or 預設值`,空字串會被換回
    # 'p' 和 'c',等於無法表達「沒有」,走位時就會去按根本不存在的技能。
    for k in ("jump_key2", "rope_key"):
        if k in m:
            out[k] = str(m[k] or "").strip().lower()[:12]
    for k in ("jump_dx", "blink_dx", "blink_dy_max"):
        try:
            out[k] = max(1, min(200, int(m.get(k, out[k]))))
        except (TypeError, ValueError):
            pass
    for k, lo, hi in (("jump_interval", 0.02, 2.0), ("jump_land", 0.0, 5.0),
                      ("rope_up", 0.0, 10.0), ("dir_hold", 0.0, 1.0),
                      ("blink_wait", 0.05, 3.0), ("blink_tail", 0.0, 1.0)):
        try:
            out[k] = max(lo, min(hi, float(m.get(k, out[k]))))
        except (TypeError, ValueError):
            pass
    return out


def _norm_skills(s):
    s = dict(s or {})
    out = dict(DEFAULT_SKILLS)
    out["summon_key"] = str(s.get("summon_key", out["summon_key"]) or "").strip().lower()[:12]
    try:
        out["summon_cd"] = max(0.0, min(3600.0, float(s.get("summon_cd", out["summon_cd"]))))
    except (TypeError, ValueError):
        pass
    for k in ("summon_while_moving", "summon_priority", "summon_pause_on_rune"):
        out[k] = bool(s.get(k, out[k]))
    return out


def _norm_attack(a):
    a = dict(a or {})
    mode = a.get("mode")
    return {"key": (str(a.get("key", "a") or "a").strip().lower()[:12] or "a"),
            "mode": mode if mode in ("tap2", "hold2s", "move") else "hold2s",
            "jump_atk": bool(a.get("jump_atk", False)),
            "fall_atk": bool(a.get("fall_atk", False))}


def list_jobs():
    """已存的職業名清單。"""
    try:
        return sorted(f[:-5] for f in os.listdir(_DIR)
                      if f.endswith(".json") and not f.startswith("_"))
    except OSError:
        return []


def get(name):
    """讀一個職業。不存在回 None。"""
    try:
        with open(_path(name), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return {"name": name, "move": _norm_move(d.get("move")),
            "attack": _norm_attack(d.get("attack")),
            "skills": _norm_skills(d.get("skills"))}


def save(name, move=None, attack=None, skills=None):
    """存/覆寫一個職業。move/attack/skills 省略時沿用既有值(沒有既有值就用預設)。"""
    name = _safe(name)
    if not name:
        return None
    old = get(name) or {"move": DEFAULT_MOVE, "attack": DEFAULT_ATTACK,
                        "skills": DEFAULT_SKILLS}
    rec = {"move": _norm_move(move if move is not None else old["move"]),
           "attack": _norm_attack(attack if attack is not None else old["attack"]),
           "skills": _norm_skills(skills if skills is not None else old.get("skills"))}
    with _lock:
        os.makedirs(_DIR, exist_ok=True)
        with open(_path(name), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    return {"name": name, **rec}


def delete(name):
    with _lock:
        try:
            os.remove(_path(name))
            return True
        except OSError:
            return False


def current_name():
    try:
        with open(_CUR_PATH, encoding="utf-8") as f:
            return str(json.load(f).get("name") or "")
    except (OSError, ValueError):
        return ""


def _set_current_name(name):
    with _lock:
        os.makedirs(_DIR, exist_ok=True)
        with open(_CUR_PATH, "w", encoding="utf-8") as f:
            json.dump({"name": name}, f, ensure_ascii=False)


def ensure_default():
    """第一次啟動時把現況存成預設職業。

    現況取自【目前生效的攻擊設定】而不是 DEFAULT_ATTACK —— 使用者早就在中控頁
    設好平A了,直接拿預設值覆蓋等於把他的設定弄丟。"""
    if list_jobs():
        return
    try:
        import mapdata
        att = mapdata.get_attack()
    except Exception:
        att = DEFAULT_ATTACK
    save(DEFAULT_NAME, DEFAULT_MOVE, att, DEFAULT_SKILLS)
    _set_current_name(DEFAULT_NAME)


def apply(name):
    """切換到某職業:攻擊設定寫回 _attack.json,移動參數注入 navigator。
    回 (ok, msg)。"""
    job = get(name)
    if job is None:
        return False, f"職業「{name}」不存在"
    try:
        import mapdata
        import navigator
        a = job["attack"]
        # 攻擊設定走既有那條路:寫進 _attack.json + 同步 navigator 的兩個開關,
        # 與 /map/attack 端點做的事完全一致(見 main.py 的 map_attack)。
        mapdata.set_attack(a["key"], a["mode"], a["jump_atk"], a["fall_atk"])
        navigator.set_jump_hold_atk(a["jump_atk"])
        navigator.set_fall_hold_atk(a["fall_atk"])
        navigator.set_move_params(job["move"])
        navigator.set_summon(job.get("skills"))
    except Exception as e:
        return False, f"套用失敗: {e!r}"
    _set_current_name(job["name"])
    return True, "ok"


def status():
    cur = current_name()
    return {"current": cur, "jobs": list_jobs(),
            "detail": get(cur) if cur else None,
            "defaults": {"move": DEFAULT_MOVE, "attack": DEFAULT_ATTACK,
                         "skills": DEFAULT_SKILLS}}
