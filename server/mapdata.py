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
import paths

# 走 paths:打包後這裡要落在 exe 旁的素材夾。若沿用 ../layouts 會指到 PyInstaller
# 的暫存解壓目錄,使用者設好的巡邏點關掉程式就消失,而且不會有任何錯誤訊息。
_DIR = paths.data_dir("layouts")
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


def _face(v):
    """放置方向正規化:'left' / 'right' / ''(不限)。認不出來的一律當不限。"""
    v = str(v or "").strip().lower()
    return v if v in ("left", "right") else ""


def _norm(p):
    """點格式正規化。相容舊 [x,y] 與缺欄位的舊 dict(face 缺=不限,行為與舊版相同)。"""
    if isinstance(p, dict):
        return {"x": int(p["x"]), "y": int(p["y"]),
                "skill": str(p.get("skill", "") or ""), "cd": float(p.get("cd", 0) or 0),
                "precise": bool(p.get("precise", False)), "skip": bool(p.get("skip", False)),
                "face": _face(p.get("face"))}
    return {"x": int(p[0]), "y": int(p[1]), "skill": "", "cd": 0.0, "precise": False,
            "skip": False, "face": ""}


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


def set_point_skill(mid, index, skill, cd, precise=False, skip=False, face=""):
    """設第 index 個巡邏點的『放置技能』鍵、冷卻秒數與精確到位模式。
    precise=True 時導航會用脈衝走路精調到 ±1px 才施放(定點召喚/圖騰用)。
    face='left'/'right' 時,施放前會再按一次該方向鍵確保面向正確;''=不限(不轉向)。
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
            p["skip"] = bool(skip)
            p["face"] = _face(face)
            d["points"][index] = p
            save(mid, d)
        return len(d["points"])


def points(mid):
    """正規化後的巡邏點清單(給導航/巡邏用):[{x,y,skill,cd,precise}, ...]。"""
    return [_norm(p) for p in load(mid).get("points", [])]


# ---- 地形:平台(可走線段 {y,xA,xB}) + 繩索(層間電梯 {x}),供跨層路徑規劃 ----
def platforms(mid):
    return [dict(p) for p in load(mid).get("platforms", [])]


def ropes(mid):
    return [dict(r) for r in load(mid).get("ropes", [])]


def add_platform(mid, y, xa, xb):
    """新增平台(記錄 A/B 兩端 x 與所在層 y)。回目前平台數。"""
    with _lock:
        d = load(mid)
        d.setdefault("platforms", []).append(
            {"y": int(y), "xA": int(min(xa, xb)), "xB": int(max(xa, xb))})
        save(mid, d)
        return len(d["platforms"])


def remove_platform(mid, index):
    with _lock:
        d = load(mid)
        pf = d.get("platforms", [])
        if 0 <= index < len(pf):
            pf.pop(index)
            save(mid, d)
        return len(d.get("platforms", []))


def add_rope(mid, x):
    """新增繩索(只記 x;覆蓋哪些層由平台幾何推斷)。回目前繩索數。"""
    with _lock:
        d = load(mid)
        d.setdefault("ropes", []).append({"x": int(x)})
        save(mid, d)
        return len(d["ropes"])


def remove_rope(mid, index):
    with _lock:
        d = load(mid)
        rp = d.get("ropes", [])
        if 0 <= index < len(rp):
            rp.pop(index)
            save(mid, d)
        return len(d.get("ropes", []))


# ---- 平A(普通攻擊)全域設定:攻擊鍵 + 施放方式(hold2s長按2秒 / tap2按兩次) ----
_ATTACK_PATH = os.path.join(_DIR, "_attack.json")


def get_attack():
    try:
        with open(_ATTACK_PATH, encoding="utf-8") as f:
            a = json.load(f)
        m = a.get("mode")
        return {"key": str(a.get("key", "a") or "a"),
                "mode": m if m in ("tap2", "hold2s", "move") else "hold2s",
                "jump_atk": bool(a.get("jump_atk", False)),
                "fall_atk": bool(a.get("fall_atk", False))}
    except Exception:
        return {"key": "a", "mode": "hold2s"}


def set_attack(key, mode, jump_atk=False, fall_atk=False):
    with _lock:
        a = {"key": (str(key or "a").strip().lower()[:12] or "a"),
             "mode": mode if mode in ("tap2", "hold2s", "move") else "hold2s",
             "jump_atk": bool(jump_atk), "fall_atk": bool(fall_atk)}
        os.makedirs(_DIR, exist_ok=True)
        with open(_ATTACK_PATH, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)
        return a


# ---- 巡邏計時全域設定:分鐘數(0=無限) ----
_PATROL_PATH = os.path.join(_DIR, "_patrol.json")
PATROL_DEFAULT_MIN = 60                # 預設 1 小時


def get_patrol_minutes():
    """巡邏時限(分鐘),0=無限。讀不到或格式壞掉回預設值。"""
    try:
        with open(_PATROL_PATH, encoding="utf-8") as f:
            v = int(json.load(f).get("minutes", PATROL_DEFAULT_MIN))
        return max(0, min(24 * 60, v))
    except Exception:
        return PATROL_DEFAULT_MIN


def set_patrol_minutes(minutes):
    with _lock:
        v = max(0, min(24 * 60, int(minutes)))
        os.makedirs(_DIR, exist_ok=True)
        with open(_PATROL_PATH, "w", encoding="utf-8") as f:
            json.dump({"minutes": v}, f, ensure_ascii=False)
        return v


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


# ---- 具名設置(profile):保存/讀取一整套「記錄點(含放置技能/精確)+平A」 ----
_PROF_DIR = paths.data_dir("profiles")


def _safe_name(name):
    """設置名 → 安全檔名(濾掉路徑非法字元;中文/英數/空白/-_()保留)。"""
    s = "".join(c for c in str(name) if c.isalnum() or c in "-_ （）()").strip()
    return s[:40] or "default"


def save_profile(name):
    """把當前地圖的巡邏點(含放置技能/精確)+平A設定,存成具名設置。回 (ok, 名稱/訊息)。"""
    mid = current_map_id()
    if not mid:
        return False, "偵測不到小地圖,無法保存"
    name = str(name or "").strip()
    if not name:
        return False, "請輸入設置名稱"
    with _lock:
        _d = load(mid)
        prof = {"name": name[:40], "map_id": mid,
                "points": [_norm(p) for p in _d.get("points", [])],
                "platforms": _d.get("platforms", []),
                "ropes": _d.get("ropes", []),
                "attack": get_attack()}
        os.makedirs(_PROF_DIR, exist_ok=True)
        with open(os.path.join(_PROF_DIR, _safe_name(name) + ".json"), "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=1)
    return True, name[:40]


def list_profiles():
    """列出所有保存的設置:[{name, map_id, count, skills}]。"""
    out = []
    try:
        for fn in sorted(os.listdir(_PROF_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_PROF_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
                pts = [_norm(p) for p in d.get("points", [])]
                out.append({"name": d.get("name", fn[:-5]), "map_id": d.get("map_id", ""),
                            "count": len(pts), "skills": sum(1 for p in pts if p.get("skill")),
                            "platforms": len(d.get("platforms", [])),
                            "ropes": len(d.get("ropes", []))})
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def load_profile(name):
    """讀取具名設置 → 寫入當前地圖的巡邏點(含放置技能),並套用平A。回 (ok, 名稱/訊息)。"""
    mid = current_map_id()
    if not mid:
        return False, "偵測不到小地圖,無法載入"
    path = os.path.join(_PROF_DIR, _safe_name(name) + ".json")
    if not os.path.exists(path):
        return False, "找不到該設置"
    with open(path, encoding="utf-8") as f:
        prof = json.load(f)

    def _wh(m):                              # "330x168" → (330,168)
        try:
            a, b = str(m).split("x")
            return int(a), int(b)
        except Exception:
            return None

    src, cur = _wh(prof.get("map_id", "")), _wh(mid)
    fx = fy = 1.0
    if src and cur and src[0] and src[1]:    # 來源尺寸→當前尺寸縮放(小地圖偵測尺寸可能變,如165→330)
        fx, fy = cur[0] / src[0], cur[1] / src[1]

    def sxx(v):
        return int(round(v * fx))

    def syy(v):
        return int(round(v * fy))

    pts = []
    for p in prof.get("points", []):
        q = _norm(p); q["x"] = sxx(q["x"]); q["y"] = syy(q["y"]); pts.append(q)
    plats = [{"y": syy(pf["y"]), "xA": sxx(pf["xA"]), "xB": sxx(pf["xB"])}
             for pf in prof.get("platforms", [])]
    rps = [{"x": sxx(r["x"])} for r in prof.get("ropes", [])]
    with _lock:                              # 只在鎖內寫;set_attack 自帶鎖,避免巢狀死鎖
        d = load(mid)
        d["points"] = pts
        d["platforms"] = plats
        d["ropes"] = rps
        save(mid, d)
    att = prof.get("attack")
    if att:
        set_attack(att.get("key", "a"), att.get("mode", "hold2s"))
    tag = f"（已縮放×{fx:.2f}）" if abs(fx - 1) > 0.01 or abs(fy - 1) > 0.01 else ""
    return True, prof.get("name", name) + tag


def delete_profile(name):
    try:
        os.remove(os.path.join(_PROF_DIR, _safe_name(name) + ".json"))
        return True
    except OSError:
        return False


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
            "platforms": d.get("platforms", []),
            "ropes": d.get("ropes", []),
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
