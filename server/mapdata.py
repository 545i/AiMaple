# -*- coding: utf-8 -*-
"""地圖座標/Layout 管理（掛機自動導航用）。

設計（依使用者需求）：
  * 每張地圖獨立記錄座標點,以小地圖尺寸(w×h)識別 → A 地圖座標不會用到 B 地圖。
  * 座標(巡邏路線點)存在 layouts/<map_id>.json,由中控設定,掛機時載入導航。
  * 掛機前必須檢查 has_layout():沒設定座標的地圖不可開掛機。
  * 記錄採容差去重,避免同一點重複堆積。

座標系為小地圖黃點相對座標(minimap.status 的 dot x/y)。
"""
import json
import os
import threading

import numpy as np
import cv2

import minimap

_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "layouts"))
_lock = threading.Lock()


def _content_hash():
    """小地圖內容 8x8 average hash(16 進位)。地形不同→hash 不同,自動區分地圖。
    8x8 縮圖把黃點/怪物等小動態元素平均掉,同圖多次穩定(段前驗證漢明距離=0)。"""
    try:
        jpg = minimap.debug_jpeg(view="crop")
        if not jpg:
            return None
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        small = cv2.resize(img, (8, 8), interpolation=cv2.INTER_AREA)
        bits = (small > small.mean()).flatten()
        return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"
    except Exception:
        return None


def current_map_id():
    """當前地圖 id = 小地圖尺寸 'WxH'(不受玩家干擾,穩定)。偵測不到回 None。

    註:原本加內容指紋(hash)想自動區分多張地圖,但小地圖上其他玩家的彩色點會讓
    hash 一直變(實測 8 次出現 5 種 id,雖只變 4/64 位),導致 layout 每次不同、
    找不到。改採【單一地圖】策略:只用尺寸——換不同尺寸地圖會自動用新 layout;
    換同尺寸地圖則由使用者在中控「清空座標 + 重新偵測」。_content_hash 函式保留,
    未來若遮罩掉玩家/怪物點,可再啟用自動多圖識別。"""
    s = minimap.status()
    if not s.get("found") or not s.get("w") or not s.get("h"):
        return None
    return f"{s['w']}x{s['h']}"


def _path(mid):
    return os.path.join(_DIR, f"{mid}.json")


def load(mid):
    if mid:
        p = _path(mid)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {"map_id": mid, "name": "", "points": []}


def save(mid, data):
    os.makedirs(_DIR, exist_ok=True)
    with open(_path(mid), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def has_layout(mid):
    """該地圖是否已設定座標(至少 1 個巡邏點)。"""
    return bool(mid) and len(load(mid).get("points", [])) > 0


def _norm(p):
    """點格式正規化。相容舊 [x,y] → {x,y,skill:'',cd:0,precise:False};新 dict 補齊欄位。"""
    if isinstance(p, dict):
        return {"x": int(p["x"]), "y": int(p["y"]),
                "skill": str(p.get("skill", "") or ""), "cd": float(p.get("cd", 0) or 0),
                "precise": bool(p.get("precise", False))}
    return {"x": int(p[0]), "y": int(p[1]), "skill": "", "cd": 0.0, "precise": False}


def add_point(mid, x, y, tol=3):
    """記錄一個巡邏點(容差去重)。回 (是否新增, 目前點數)。"""
    with _lock:
        d = load(mid)
        for p in d["points"]:
            pp = _norm(p)
            if abs(pp["x"] - x) <= tol and abs(pp["y"] - y) <= tol:
                return False, len(d["points"])
        d["points"].append({"x": int(x), "y": int(y), "skill": "", "cd": 0.0})
        save(mid, d)
        return True, len(d["points"])


def set_point_skill(mid, index, skill, cd, precise=False):
    """設第 index 個巡邏點的『放置技能』鍵、冷卻秒數與精確到位模式。
    precise=True 時導航會用脈衝走路精調到 ±1px 才施放(定點召喚/圖騰用)。
    空鍵=取消該點放置技能。回目前點數。"""
    with _lock:
        d = load(mid)
        if 0 <= index < len(d["points"]):
            p = _norm(d["points"][index])
            p["skill"] = str(skill or "").strip().lower()[:12]
            try:
                p["cd"] = max(0.0, float(cd or 0))
            except Exception:
                p["cd"] = 0.0
            p["precise"] = bool(precise)
            d["points"][index] = p
            save(mid, d)
        return len(d["points"])


def points(mid):
    """正規化後的巡邏點清單(給導航/巡邏用):[{x,y,skill,cd}, ...]。"""
    return [_norm(p) for p in load(mid).get("points", [])]


# ---- 平A(普通攻擊)全域設定:攻擊鍵 + 施放方式(hold2s長按2秒 / tap2按兩次) ----
_ATTACK_PATH = os.path.join(_DIR, "_attack.json")


def get_attack():
    try:
        with open(_ATTACK_PATH, encoding="utf-8") as f:
            a = json.load(f)
        return {"key": str(a.get("key", "a") or "a"),
                "mode": "tap2" if a.get("mode") == "tap2" else "hold2s"}
    except Exception:
        return {"key": "a", "mode": "hold2s"}


def set_attack(key, mode):
    with _lock:
        a = {"key": (str(key or "a").strip().lower()[:12] or "a"),
             "mode": "tap2" if mode == "tap2" else "hold2s"}
        os.makedirs(_DIR, exist_ok=True)
        with open(_ATTACK_PATH, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)
        return a


def remove_last(mid):
    """移除最後一個記錄的點(設定時手滑用)。回目前點數。"""
    with _lock:
        d = load(mid)
        if d["points"]:
            d["points"].pop()
            save(mid, d)
        return len(d["points"])


def clear(mid):
    """清空該地圖座標(重新設定用)。"""
    with _lock:
        p = _path(mid)
        if os.path.exists(p):
            os.remove(p)


def set_name(mid, name):
    with _lock:
        d = load(mid)
        d["name"] = str(name)[:40]
        save(mid, d)


def status():
    """當前地圖狀態(供中控顯示)。map_id 為 None 表示偵測不到小地圖。"""
    mid = current_map_id()
    d = load(mid) if mid else {"name": "", "points": []}
    pts = [_norm(p) for p in d.get("points", [])]
    return {"map_id": mid,
            "has_layout": has_layout(mid),
            "name": d.get("name", ""),
            "count": len(pts),
            "points": pts,
            "attack": get_attack()}


def list_maps():
    """已設定過的所有地圖(檔案列表)。"""
    out = []
    if os.path.isdir(_DIR):
        for fn in os.listdir(_DIR):
            if fn.endswith(".json"):
                mid = fn[:-5]
                d = load(mid)
                out.append({"map_id": mid, "name": d.get("name", ""),
                            "count": len(d.get("points", []))})
    return out
