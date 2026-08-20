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
import json
import os
import random
import threading
import time

import minimap
import nav_trace
import pathgraph
import paths

_keyboard = None
_focus_fn = None
_thread = None
_stop = threading.Event()
_user_stop = threading.Event()   # 使用者按下停止(有別於流程內部的 stop);見 stop()
_op_lock = threading.Lock()
_state = {"running": False, "phase": "idle", "target": None,
          "pos": None, "arrived": False, "error": "", "steps": 0,
          "stuck_n": 0, "stuck_ok": 0, "stuck_last": None}
_place = {}   # (x,y) → {"skill","cd","last"(monotonic|None)};放置技能冷卻狀態,供監控下次觸發

# ---- 向量/容差參數 ----
# 【JUMP_DX / JUMP_INTERVAL / 下面的按鍵與等待都屬於「職業」】預設值量自職業「蓮」,
# 由 jobs.set_move_params() 在切換職業時覆寫。換職業這些全都不一樣:位移距離、
# 技能鍵、動作前後搖。X_TOL/Y_TOL/FALL_MAX 這類是小地圖解析度與閉環邏輯的參數,
# 與職業無關,不在覆寫範圍。
JUMP_DX = 30          # 二段跳一次水平飛距(px)
X_TOL = 3             # 水平到達容差
Y_TOL = 4             # 垂直到達容差
JUMP_INTERVAL = 0.20  # X→P 的間隔(down-to-down)
KEY_HOLD = 0.06
JUMP_K1 = "x"         # 二段跳第一段(下跳與卡住脫困也用它)
JUMP_K2 = "p"         # 二段跳第二段。空字串 = 此職業【沒有】二段跳,只跳一段
ROPE_K = "c"          # 上繩。空字串 = 不用繩索(瞬移職業直接上下層)
JUMP_LAND = 1.0       # 二段跳後等落地
ROPE_UP_WAIT = 1.6    # 上繩到頂

# 移動方式。兩種職業的位移原語根本不同,不是換個按鍵就好:
#   double_jump  二段跳飛固定距離 + 繩索上樓 + 下跳落到正下方(蓮)
#   blink        瞬移:水平固定距離、【垂直直接到相鄰平台】(陰陽師)
# 差別最大的是垂直:二段跳職業要走到繩索的確切 x 才上得去,瞬移職業在任意 x 都能上下,
# 所以路徑規劃與走位流程都要分兩套。
MOVE_TYPE = "double_jump"
BLINK_K = "v"         # 瞬移鍵
BLINK_DX = 28         # 一次瞬移的水平距離
DIR_HOLD = 0.15       # 按瞬移鍵之前方向鍵要先按住多久。實測 0.06 完全不觸發(位移 0),
                      # 遊戲要先認到方向鍵按住的狀態,瞬移才會朝那個方向走。
BLINK_WAIT = 0.6      # 瞬移後等動作結束
BLINK_DY_MAX = 24     # 垂直瞬移一次最遠跨多少層距(見 jobs.DEFAULT_MOVE 的說明)
BLINK_TAIL = 0.08     # 放開方向鍵後的緩衝
BLINK_UP_MAX = 10     # 【水平】瞬移能順帶爬升多少層距(見 jobs.DEFAULT_MOVE)


FALL_MAX = 8          # 下跳閉環最多跳幾次
PRECISE_X_TOL = 1     # 精確模式水平容差(px):二段跳到位後(可過衝)再走路回正到 ±1
PRECISE_STEPS = 10    # 精確走路回正最多步數
X_MAX_STEPS = 14      # 水平閉環步數上限


def set_move_params(m):
    """套用職業的移動參數(由 jobs.apply 呼叫)。缺的欄位保留現值。"""
    global JUMP_DX, JUMP_INTERVAL, JUMP_K1, JUMP_K2, ROPE_K, JUMP_LAND, ROPE_UP_WAIT
    global MOVE_TYPE, BLINK_K, BLINK_DX, DIR_HOLD, BLINK_WAIT, BLINK_DY_MAX
    global BLINK_TAIL, BLINK_UP_MAX
    m = dict(m or {})
    JUMP_DX = int(m.get("jump_dx", JUMP_DX))
    JUMP_INTERVAL = float(m.get("jump_interval", JUMP_INTERVAL))
    JUMP_K1 = str(m.get("jump_key1", JUMP_K1))
    JUMP_K2 = str(m.get("jump_key2", JUMP_K2))
    ROPE_K = str(m.get("rope_key", ROPE_K))
    JUMP_LAND = float(m.get("jump_land", JUMP_LAND))
    ROPE_UP_WAIT = float(m.get("rope_up", ROPE_UP_WAIT))
    t = str(m.get("type", MOVE_TYPE) or MOVE_TYPE)
    MOVE_TYPE = t if t in ("double_jump", "blink") else "double_jump"
    BLINK_K = str(m.get("blink_key", BLINK_K))
    BLINK_DX = int(m.get("blink_dx", BLINK_DX))
    DIR_HOLD = float(m.get("dir_hold", DIR_HOLD))
    BLINK_WAIT = float(m.get("blink_wait", BLINK_WAIT))
    BLINK_DY_MAX = int(m.get("blink_dy_max", BLINK_DY_MAX))
    BLINK_TAIL = float(m.get("blink_tail", BLINK_TAIL))
    BLINK_UP_MAX = int(m.get("blink_up_max", BLINK_UP_MAX))
    if MOVE_TYPE == "blink":
        print(f"[nav] 移動參數 type=瞬移 鍵={BLINK_K} 距離={BLINK_DX} "
              f"dir_hold={DIR_HOLD} 等待={BLINK_WAIT} 跳躍鍵={JUMP_K1}({JUMP_DX})")
    else:
        print(f"[nav] 移動參數 type=二段跳 跳躍鍵={JUMP_K1}+{JUMP_K2} 繩索鍵={ROPE_K} "
              f"飛距={JUMP_DX} 間隔={JUMP_INTERVAL} 落地={JUMP_LAND} 上繩={ROPE_UP_WAIT}")


def step_dx():
    """目前職業一次水平位移原語能移動多遠。給 pathgraph 建圖與起跳點計算共用 ——
    寫死 30 會讓瞬移職業規劃出跳不到的邊。"""
    return BLINK_DX if MOVE_TYPE == "blink" else JUMP_DX


# ---- 召喚(主要輸出技能) ----
# 有些職業的傷害幾乎全來自召喚物(陰陽師:紅 55s / 紫 25s,冷卻 60s),冷卻一好就該補,
# 不能等走到巡邏點才放 —— 那會白白空轉一段冷卻。所以放在每輪【開頭】,先於走位。
_summon = {"key": "", "cd": 0.0, "while_moving": True, "priority": True, "last": None}


def set_summon(cfg):
    """由 jobs.apply 注入。key 空字串 = 此職業沒有召喚。"""
    cfg = dict(cfg or {})
    _summon.update({
        "key": str(cfg.get("summon_key", "") or "").strip(),
        "cd": float(cfg.get("summon_cd", 0) or 0),
        "while_moving": bool(cfg.get("summon_while_moving", True)),
        "priority": bool(cfg.get("summon_priority", True)),
    })
    _summon["last"] = None                 # 換職業後冷卻重算,別沿用上一個職業的計時
    if _summon["key"]:
        print(f"[nav] 召喚 鍵={_summon['key']} 冷卻={_summon['cd']}s "
              f"移動中可放={_summon['while_moving']} 優先={_summon['priority']}")


def summon_ready():
    if not _summon["key"] or _summon["cd"] <= 0:
        return False
    last = _summon["last"]
    return last is None or (time.monotonic() - last) >= _summon["cd"]


def _cast_summon():
    """放召喚並記下時間。回是否真的放了。"""
    if not summon_ready():
        return False
    try:
        _press(_summon["key"], hold=0.12)
    except Exception as e:
        print(f"[nav] 召喚失敗: {e!r}")
        return False
    _summon["last"] = time.monotonic()
    _state["summon_n"] = _state.get("summon_n", 0) + 1
    print(f"[nav] 放召喚(第 {_state['summon_n']} 次)")
    time.sleep(0.25)                       # 施放後搖,太快接走位會被吃掉
    return True


def summon_remaining():
    """距離下次可放還有幾秒;沒有召喚回 None,已就緒回 0。"""
    if not _summon["key"] or _summon["cd"] <= 0:
        return None
    if _summon["last"] is None:
        return 0.0
    return max(0.0, _summon["cd"] - (time.monotonic() - _summon["last"]))

# ---- 卡住脫困 ----
# 實測角色會卡在地圖的爬梯處:走位鍵按下去沒反應,位置完全不動。要【按住左右其中
# 一個方向鍵 + 跳躍鍵】才出得來 —— 單按方向鍵或單獨跳都無效。
#
# 為什麼需要獨立看門狗而不是在移動迴圈裡檢查:卡住時主執行緒正阻塞在某個移動函式
# 裡(最壞是 _rope_to 掃 6 個偏移,每次含 1.6s 固定等待),它不會自己回來檢查。
# 各移動函式內部雖然已有短程的 stuck 自癒(二段跳撞牆→改走路),但那是「這一步沒效」
# 等級的處理,救不了「整個角色被地形黏住」。
STUCK_SECS = 10.0     # 移動階段連續多久沒位移視為卡住
STUCK_TOL = 1         # 位置變化 ≤ 此值視為沒動(黃點靜止時 0px 抖動,見移動模型)
STUCK_POLL = 0.5      # 看門狗取樣間隔
UNSTICK_DIR = "right"  # 脫困方向。固定一邊即可 —— 使用者實測左右都出得來,
                       # 交替只會讓選錯的那一半白等一輪 STUCK_SECS。
UNSTICK_JUMPS = 2     # 一次脫困跳幾下
UNSTICK_HOLD = 0.5    # 跳完再按住方向鍵多久(讓角色真的走離卡點)
UNSTICK_LAND = 0.6    # 動作結束到判定之間的等待:跳躍途中的位置不算數,要等落地
REPLAN_MAX = 3        # 脫困後重新規劃路線的次數上限(防無限迴圈)

# 只在【移動中】計時。到點施放技能、等放置技能冷卻、紫標暫停等階段角色本來就靜止,
# 把那些算進去會不停誤觸發脫困 —— 巡邏只剩冷卻中的點時甚至會整分鐘站著不動。
#
# 刻意【不含 fine_x】:那是 ±1px 的精確微調,而 STUCK_TOL=1 會把 1px 的位移看成
# 「沒動」,反覆微調就可能被誤判成卡住,一腳把角色跳離剛走準的位置。該階段最多
# PRECISE_STEPS x ~0.25s ≈ 2.5 秒,本來就用不到 10 秒的卡住保護。
MOVING_PHASES = {"g_walk", "g_jump", "g_fall", "g_rope", "move_x",
                 "pre_move_x", "fall", "fall_adjust", "rope_up"}


def set_keyboard(kb):
    global _keyboard
    _keyboard = kb


def set_focus_fn(fn):
    global _focus_fn
    _focus_fn = fn


_terrain_fn = None
# 繩索位置的讀/寫。規劃器沒有它就只能拿「平台重疊區中點」猜,實測差 4~6 格,
# 導致每次上繩的第一按必定失敗(見 mapdata.note_rope)。
_ropes_fn = None      # () -> [{"x": int}, ...]
_note_rope_fn = None  # (x) -> None  上繩成功時回報真正有效的 x


def set_rope_fns(get_fn=None, note_fn=None):
    """注入繩索位置的讀/寫(由 main 綁到 mapdata)。"""
    global _ropes_fn, _note_rope_fn
    if get_fn is not None:
        _ropes_fn = get_fn
    if note_fn is not None:
        _note_rope_fn = note_fn


def set_terrain_fn(fn):
    """注入地形來源 fn()→(points_dicts, platforms)。有平台時導航改用平台重疊圖規劃。"""
    global _terrain_fn
    _terrain_fn = fn


_round_hook = None


def set_round_hook(fn):
    """巡邏每輪開頭呼叫 fn();回 True 代表「本輪有事要處理,先別走位」。
    用 hook 而非直接 import,是為了不讓 navigator 認識復活/符文這些上層功能。"""
    global _round_hook
    _round_hook = fn


# ---- 巡邏時限 ----
# 刻意【不放在 patrol_start 的參數裡】:解符文完成後會再呼叫一次 patrol_start 接回巡邏,
# 若期限跟著重算,每解一次符文就把掛機時間往後延,設定的時限等於失效。
# 由 main 在「使用者按下開始巡邏」時設定一次,中途重啟巡邏不動它。
_patrol_deadline = None                # monotonic 秒;None=無限


def set_patrol_deadline(seconds):
    """設定巡邏時限。seconds<=0 或 None → 無限。"""
    global _patrol_deadline
    _patrol_deadline = None if not seconds or seconds <= 0 \
        else time.monotonic() + float(seconds)
    return _patrol_deadline


def patrol_remaining():
    """剩餘秒數;無限回 None,已到期回 0。"""
    if _patrol_deadline is None:
        return None
    return max(0.0, _patrol_deadline - time.monotonic())


def _patrol_time_up():
    return _patrol_deadline is not None and time.monotonic() >= _patrol_deadline


_timer_thread = None
_timer_gen = 0                         # 世代號:舊看門狗靠它自我退場


def _patrol_timer(gen):
    """看門狗:時限一到立刻停,不必等當前那一輪走位跑完(一輪可能十幾秒)。

    gen 是必要的:patrol_start 會 `_stop.clear()`,若只靠 _stop 當退場條件,舊看門狗
    會在下次啟動時復活 → 累積多條。而巡邏會被解符文流程反覆停止/接回,這條路徑很常走。"""
    while gen == _timer_gen and not _stop.is_set():
        rem = patrol_remaining()
        if rem is None:                # 中途被改成無限
            return
        if rem <= 0:
            if _state.get("running"):
                _state["phase"] = "time_up"
                _state["error"] = "巡邏時間到,已自動停止"
                print("[nav] 巡邏時間到 → 停止(看門狗)")
            _stop.set()                # 只設事件,不 join(避免跟巡邏執行緒互等)
            return
        if _stop.wait(min(rem, 1.0)):
            return


# ---- 移動數據記錄(每段動作:起點/目標/落點),供參考與訓練移動模型 ----
_MOVE_LOG = os.path.join(paths.data_dir("logs"), "nav_moves.jsonl")


_MOVE_LOG_MAX = 8 * 1024 * 1024        # 8MB 上限:超過就輪替成 .1(只留一代)
_move_log_n = 0


def _log_move(mode, act, start, target, end):
    """追加一筆移動記錄。原本無上限成長(實測掛機累積到 1.3MB/12000 筆仍在長),
    長期掛機會把磁碟慢慢吃掉,也讓後續分析要掃越來越大的檔。加上大小輪替。

    【為什麼要記 map_id】start/end 是「角色確實站得住的位置」—— 那本身就是一份
    自動累積的地形資料,比手標平台完整(實測 597 個去重位置裡有 122 個落在已標平台外,
    其中 y=34 那批正是梯子處,導航就是在那裡失敗的)。但沒有 map_id 就無法按地圖分離,
    多張圖的座標混在一起等於不能用。參考 auto-maple 的 Layout:它在每次移動後
    記錄角色位置,路徑規劃直接在這些「走過的點」上做 A*,完全不需要手標地形。"""
    global _move_log_n
    try:
        mid = ""
        try:
            import mapdata
            mid = mapdata.current_map_id() or ""
        except Exception:
            pass
        with open(_MOVE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 1), "map": mid,
                                "mode": mode, "act": act,
                                "start": start, "target": target, "end": end},
                               ensure_ascii=False) + "\n")
        _move_log_n += 1
        if _move_log_n % 200 == 0:                 # 每 200 筆才 stat 一次,省 I/O
            if os.path.getsize(_MOVE_LOG) > _MOVE_LOG_MAX:
                os.replace(_MOVE_LOG, _MOVE_LOG + ".1")
                print(f"[nav] 移動記錄超過 {_MOVE_LOG_MAX // 1048576}MB → 輪替")
    except Exception:
        pass


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
                   "face": v.get("face", ""),
                   "remaining": round(rem, 1), "ready": rem <= 0.05})
    s["placements"] = pl
    rem = patrol_remaining()
    s["patrol_remaining"] = None if rem is None else round(rem, 1)
    s["pass_n"] = _state.get("pass_n", 0)
    sr = summon_remaining()
    s["summon"] = None if sr is None else {
        "key": _summon["key"], "cd": _summon["cd"],
        "remaining": round(sr, 1), "ready": sr <= 0.05,
        "n": _state.get("summon_n", 0)}
    s["move_type"] = MOVE_TYPE
    return s


# ---------- 底層動作 ----------
def _dot():
    """讀一次小地圖黃點 (x,y)。抓不到回 None。

    【每一次讀值都會進軌跡記錄】記錄點刻意放在這裡而不是另開取樣執行緒 ——
    要查的是「導航器讀到移動中的瞬時值就下判斷」,所以要記的是它【實際看到的
    輸入】,不是另外量到的真相。細節見 server/nav_trace.py 檔頭。"""
    try:
        minimap.detect_once()
        s = minimap.status()
        d = s.get("dot")
        if d and s.get("found") and not s.get("dot_stale"):
            nav_trace.sample(d["x"], d["y"], _state.get("phase"))
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
    nav_trace.event("press", k, _state.get("phase"))
    _keyboard.key_down(k)
    time.sleep(hold)
    _keyboard.key_up(k)


_atk_held = set()
_jump_hold_atk = False   # 二段跳時是否保持攻擊鍵(不放開);由平A設定勾選
_fall_hold_atk = False   # 下跳時是否保持攻擊鍵


def set_jump_hold_atk(v):
    global _jump_hold_atk
    _jump_hold_atk = bool(v)


def set_fall_hold_atk(v):
    global _fall_hold_atk
    _fall_hold_atk = bool(v)


def _release_atk():
    """放開所有按住中的移動攻擊鍵(繩索/下跳/二段跳/到點時,避免中斷垂直動作)。"""
    for sk in list(_atk_held):
        try:
            _keyboard.key_up(sk)
        except Exception:
            pass
    _atk_held.clear()


FACE_HOLD = 0.06          # 轉向按住時間。與中控頁 /face 相同 —— 實測純 tap(放開太快)
                          # 遊戲會漏讀導致轉向失敗,60ms 才穩。
FACE_SETTLE = 0.12        # 轉向後到施放之間的緩衝(讓轉身動作結束)


def _face_for_place(face):
    """放置技能施放前先確保面向。face='left'/'right';空值=不限,完全不動作。

    【為什麼需要】召喚/圖騰類技能會朝角色面向放置,而角色的面向取決於走位過程中
    最後按的方向鍵 —— 從左邊走到點就朝右、從右邊走到點就朝左,同一個點每輪面向可能
    不同,放出來的位置就飄。指定方向後每次施放前重按一次該方向鍵,面向才可重現。

    【副作用要知道】遊戲沒有「只轉向不移動」的輸入,按方向鍵一定會走一小步
    (60ms ≈ 幾個像素)。已勾「精確到位」的點要留意這個位移;不需要固定面向就選不限。"""
    if face not in ("left", "right"):
        return
    # 【不做「已經朝這邊就跳過」的最佳化】keyboard.last_dir 只反映「本程式最後按過的
    # 方向」,使用者手動操作、被怪物擊退、或上一輪的殘留都可能讓它與實際面向不一致。
    # 無條件重按一次的代價只是幾個像素,比放錯位置便宜。
    _press(face, hold=FACE_HOLD)
    time.sleep(FACE_SETTLE)


# ---------- 卡住脫困看門狗 ----------
_stuck_thread = None
_stuck_gen = 0            # 世代號:舊執行緒靠它自我退場(同 minimap._watch_loop 的做法)
_replan = threading.Event()   # 脫困成功 → 位置已變,原路徑作廢,通知主流程重新規劃


def _unstick():
    """脫困動作 + 驗證。回是否真的脫離。

    動作是【按住方向鍵 + 跳】,單按方向鍵或單獨跳都出不來(使用者實測)。
    方向固定一邊就好 —— 左右都能脫離,交替只會讓選錯的那一半白等一輪 STUCK_SECS。

    先放開所有鍵再動作:卡住時主執行緒很可能正按著某個方向鍵,不清乾淨會互相抵銷。

    【怎麼驗證】比對動作前後的位置。關鍵是【等落地停穩再讀】—— 跳躍途中黃點正在
    移動,那時的讀數會把「跳起來又落回原地」誤判成脫困成功。這是移動模型裡既有的
    原則:黃點靜止時 0px 抖動、讀數極準,移動中的瞬時值不可信。"""
    p0 = _dot()
    print(f"[nav] 卡住 {STUCK_SECS:.0f}s → 脫困(方向={UNSTICK_DIR},起點={p0})")
    _release_atk()
    for k in ("left", "right", "up", "down"):
        try:
            _keyboard.key_up(k)
        except Exception:
            pass
    try:
        _keyboard.key_down(UNSTICK_DIR)
        for _ in range(UNSTICK_JUMPS):
            if _stop.is_set():
                return False
            _press(JUMP_K1)
            time.sleep(0.3)
        time.sleep(UNSTICK_HOLD)
    except Exception as e:
        print(f"[nav] 脫困動作失敗: {e!r}")
        return False
    finally:
        try:
            _keyboard.key_up(UNSTICK_DIR)
        except Exception:
            pass
    time.sleep(UNSTICK_LAND)               # 等落地,別拿空中的位置判定
    p1 = _settle(retries=6)
    if p0 is None or p1 is None:
        return False
    moved = abs(p1[0] - p0[0]) > STUCK_TOL or abs(p1[1] - p0[1]) > STUCK_TOL
    print(f"[nav] 脫困{'成功' if moved else '無效'} {p0} → {p1}")
    return moved


def _stuck_watch(gen):
    """移動階段連續 STUCK_SECS 沒位移就脫困。自己讀黃點而不是看 _state["pos"]:
    各段更新 pos 的頻率不一,有的段(如二段跳)整段都不讀,靠它會把「沒人更新」
    誤當成「沒有移動」。"""
    last, since = None, None
    # running 轉 False 就退場:單次 move_to 跑完不會 set _stop,少了這個條件
    # 看門狗會一路空轉到下次導航才被世代號換掉。
    while not _stop.is_set() and _stuck_gen == gen and _state.get("running"):
        time.sleep(STUCK_POLL)
        if _state.get("phase") not in MOVING_PHASES:
            last, since = None, None       # 非移動階段不計時,離開後從頭算
            continue
        p = _dot()
        if p is None:
            continue                       # 讀不到黃點不代表沒動,不累計
        now = time.monotonic()
        if last is None or abs(p[0] - last[0]) > STUCK_TOL or abs(p[1] - last[1]) > STUCK_TOL:
            last, since = p, now
            continue
        if since is not None and now - since >= STUCK_SECS:
            _state["stuck_n"] = _state.get("stuck_n", 0) + 1
            _state["stuck_last"] = round(time.time(), 1)
            if _unstick():
                # 脫出後位置已經不在原路徑上了,沿用舊路徑會照著過期的起點走。
                # 通知主流程重新規劃 —— 移動迴圈看到這個旗標會提早收手。
                _state["stuck_ok"] = _state.get("stuck_ok", 0) + 1
                _replan.set()
            last, since = None, None       # 無論成敗都重新計時,不要連續狂按


def _stuck_start():
    """開一條看門狗;舊的靠世代號自己退場。導航/巡邏啟動時呼叫。"""
    global _stuck_thread, _stuck_gen
    _replan.clear()           # 上一趟殘留的重規劃旗標會讓新導航一開始就自我中斷
    _stuck_gen += 1
    _stuck_thread = threading.Thread(target=_stuck_watch, args=(_stuck_gen,), daemon=True)
    _stuck_thread.start()


def _release_move_keys():
    """放開所有移動鍵 + 按住的攻擊鍵,確保靜止/垂直動作時不干擾。"""
    for k in ("left", "right", "up", "down"):
        try:
            _keyboard.key_up(k)
        except Exception:
            pass
    _release_atk()


def _same_skills(skills):
    """移動走位時【按住】攻擊鍵(持續攻擊,支援按住連發的技能)。已按住則不重複 key_down;
    繩索/下跳/二段跳/到點由 _release_atk 放開,避免中斷垂直動作。"""
    if not skills:
        return
    for sk in skills:
        if sk not in _atk_held:
            try:
                _keyboard.key_down(sk)
                _atk_held.add(sk)
            except Exception:
                pass


def _double_jump(direction, skills=None):
    """朝 direction 二段跳(X→interval→P)。預設跳前放開攻擊鍵(避免與 X/P 衝突);
    勾「二段跳保持攻擊」則不放、跳後重新按住(某些技能支援邊跳邊連發)。

    JUMP_K2 為空 = 此職業沒有二段跳,只按第一段(實測陰陽師 X 單跳 18px,
    連按兩次只有 21px —— 那 3px 是落地殘餘不是第二段)。"""
    if not _jump_hold_atk:
        _release_atk()
    _press(direction)                      # 轉向(press,確保面向)
    time.sleep(0.12)
    _keyboard.key_down(JUMP_K1); time.sleep(KEY_HOLD); _keyboard.key_up(JUMP_K1)
    if JUMP_K2:
        time.sleep(max(0.0, JUMP_INTERVAL - KEY_HOLD))
        _keyboard.key_down(JUMP_K2); time.sleep(KEY_HOLD); _keyboard.key_up(JUMP_K2)
    if _jump_hold_atk:
        _same_skills(skills)               # 保持攻擊:跳後重新按住
    time.sleep(JUMP_LAND)                  # 等落地(職業參數)


def _blink(direction, skills=None):
    """朝 direction 瞬移一次(方向鍵按住 DIR_HOLD → 按瞬移鍵 → 放開)。

    【方向鍵在整段期間都按著,所以位移 = 瞬移 + 走路】這點量錯過:原本以為「瞬移 28px」,
    其實 28 裡有一段是 BLINK_WAIT 期間繼續走路走出來的。把 wait 從 0.60 壓到 0.20,
    位移掉到 20 —— 差的 8px 正好對應 0.4 秒走路(實測走路 16px/秒)。
    但【效率反而變好】:0.60 那組是 28px/1.19s = 23.5px/秒,0.20 這組是 20px/0.59s
    = 33.9px/秒,快了 44%。所以參數要照「每秒移動多少」挑,不是照「一次走多遠」。

    【DIR_HOLD 一度以為要 0.15】那是量測時遊戲沒有焦點造成的假象(按鍵全送進終端機,
    位移當然是 0)。焦點正確後 0.06 一樣觸發,取 0.08 留點餘裕。"""
    _release_atk()                         # 瞬移是位移技能,按住攻擊鍵會互相搶輸入
    _keyboard.key_down(direction)
    try:
        time.sleep(DIR_HOLD)
        _press(BLINK_K)
        time.sleep(BLINK_WAIT)
    finally:
        _keyboard.key_up(direction)
    time.sleep(BLINK_TAIL)


def _blink_v(up, skills=None):
    """垂直瞬移一次:up=True 往上、False 往下。回是否真的移動了。

    【與二段跳職業的差別】瞬移直接落到【上/下最近的可站平台】,任意 x 位置都行,
    不需要走到繩索的確切位置。實測 (68,49)→(68,38)→(68,23),每次都停在最近那層。
    走不動(該方向沒有平台)時回 False,由呼叫端決定要不要換水平位置再試。"""
    a = _dot()
    _blink("up" if up else "down", skills)
    b = _dot()
    if a is None or b is None:
        return False
    return abs(b[1] - a[1]) > Y_TOL


def _walk_to_x(direction, target_x, skills=None, timeout_s=2.5):
    """按住方向鍵走到 target_x(±X_TOL),過程中同時按技能鍵。撞牆/逾時停。"""
    _keyboard.key_down(direction)
    t0 = time.monotonic(); last = None; stuck = 0
    try:
        while (time.monotonic() - t0 < timeout_s
               and not _stop.is_set() and not _replan.is_set()):
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
    """閉環下跳到目標 y。預設下跳期間放開攻擊鍵;勾「下跳保持攻擊」則保持按住。"""
    if not _fall_hold_atk:
        _release_atk()
    _keyboard.key_down("down")
    time.sleep(0.2)
    try:
        for _ in range(FALL_MAX):
            if _stop.is_set() or _replan.is_set():
                break
            p = _dot()
            if p:
                _state["pos"] = list(p)
                if p[1] >= ty - Y_TOL:
                    break
            nav_trace.event("fall_jump", JUMP_K1, _state.get("phase"))
            _keyboard.key_down(JUMP_K1); time.sleep(0.08); _keyboard.key_up(JUMP_K1)
            if _fall_hold_atk:
                _same_skills(skills)
            time.sleep(0.45)
    finally:
        _keyboard.key_up("down")
    time.sleep(0.3)


def _rope_up(skills=None):
    """繩索 C 上升到第 4 層(不中斷)。上繩前放開攻擊鍵。"""
    _release_atk()
    _press(ROPE_K)
    time.sleep(ROPE_UP_WAIT)


def _hop(direction, skills=None):
    """一次水平位移原語:瞬移職業用瞬移,其餘用二段跳。"""
    if MOVE_TYPE == "blink":
        _blink(direction, skills)
    else:
        _double_jump(direction, skills)


# ---------- 導航主邏輯 ----------
def _move_to_x(tx, skills=None):
    """水平閉環:遠用位移原語(瞬移/二段跳)、近用走路;卡住(x 沒變)自癒——換走路脫困。"""
    last_x = None
    for i in range(X_MAX_STEPS):
        if _stop.is_set() or _replan.is_set():   # 脫困過 → 目標與路徑都要重算,別再往舊 tx 走
            return
        p = _dot()
        if not p:
            continue
        _state["pos"] = list(p); _state["steps"] += 1
        if _maybe_cast_passing(p):      # 路過巡邏點 → 就地平A,打完重讀位置再繼續
            continue
        dx = tx - p[0]
        if abs(dx) <= X_TOL:
            return
        d = "right" if dx > 0 else "left"
        # 卡住自癒:上一步後 x 幾乎沒變 → 位移原語撞牆/無效,改走路脫困
        stuck = last_x is not None and abs(p[0] - last_x) <= 1
        last_x = p[0]
        if abs(dx) > step_dx() - 5 and not stuck:
            _hop(d, skills)
        else:
            _walk_to_x(d, tx, skills)


def _fine_tune_x(tx, tol=PRECISE_X_TOL):
    """精確回正(實測驗證版):每步先 _settle 等角色【完全停穩】再讀黃點判定——
    黃點靜止時 0px 抖動、讀數極準;而移動中/慣性讀取會把『經過瞬間』誤判成到位
    (放置技能因此放不準)。未達則走路脈衝,依實測位移分檔:≥3px→0.12s、2px→0.08s、
    1px→0.06s(走路慢、慣性極小,故小步即可)。實測從偏移 8px 約 4~5 步收斂到 ±1px。"""
    for _ in range(PRECISE_STEPS):
        if _stop.is_set():
            return False
        p = _settle(retries=10)          # 關鍵:停穩後才判定,不用移動中瞬時值
        if not p:
            continue
        _state["pos"] = list(p)
        dx = tx - p[0]
        if abs(dx) <= tol:
            return True
        adx = abs(dx)
        dur = 0.12 if adx >= 3 else (0.08 if adx == 2 else 0.06)   # 實測分檔
        d = "right" if dx > 0 else "left"
        _keyboard.key_down(d)
        time.sleep(dur)
        _keyboard.key_up(d)
    return False


# ---------- 平台圖導航(按鍵流程):沿 pathgraph 路徑分段執行 ----------
def _walk_to(tx, tol=1):
    """走路脈衝到 tx(小範圍精走,繩索掃描/連接點對齊用)。"""
    for _ in range(12):
        if _stop.is_set():
            return
        p = _dot()
        if not p:
            continue
        _state["pos"] = list(p)
        dx = tx - p[0]
        if abs(dx) <= tol:
            return
        d = "right" if dx > 0 else "left"
        adx = abs(dx)
        dur = 0.25 if adx > 6 else (0.12 if adx >= 3 else (0.08 if adx == 2 else 0.06))
        _keyboard.key_down(d); time.sleep(dur); _keyboard.key_up(d)
        time.sleep(0.1)


def _jump_up(ty):
    """二段跳上一層平台:朝面向二段跳(有垂直分量),確認 y 上升到目標層(最多 4 次)。"""
    for _ in range(4):
        if _stop.is_set():
            return
        p = _dot()
        if p and p[1] <= ty + Y_TOL:
            return
        d = getattr(_keyboard, "last_dir", None) or "right"
        _double_jump(d)
        time.sleep(0.2)


def _try_rope_here(ty):
    """當前位置按 C:若上升則上到頂+下跳修正到 ty,回 True;沒上升回 False。"""
    p0 = _settle()
    if not p0:
        return False
    _rope_up()
    p1 = _dot() or p0
    if p1[1] < p0[1] - 3:                   # y 變小=上升成功
        # 【把真正上得去的 x 記起來】規劃器沒有繩索資料時是猜重疊區中點,實測差
        # 4~6 格,所以每次上繩的第一按都必定失敗、要靠下面的偏移掃描才找得到,
        # 每次固定浪費約 2.3 秒。記一次之後,下次規劃就直接走到對的位置。
        if _note_rope_fn:
            try:
                _note_rope_fn(int(p0[0]))
            except Exception:
                pass                        # 記錄失敗不能影響走位
        nav_trace.event("rope_ok", ROPE_K, _state.get("phase"), note=f"x={p0[0]}")
        if p1[1] < ty - Y_TOL:             # 上過頭 → 下跳修正到目標層
            _fall_to_y(ty)
        return True
    nav_trace.event("rope_fail", ROPE_K, _state.get("phase"), note=f"x={p0[0]}")
    return False


def _rope_to(nx, ty):
    """繩索上到目標層:在 nx 附近找繩索(按C測,不行沿重疊區小幅掃)→上頂→下跳修正。"""
    if _try_rope_here(ty):
        return True
    for off in (-4, 4, -8, 8, -12, 12):    # 附近掃繩索(重疊區內)
        if _stop.is_set():
            return False
        _walk_to(nx + off)
        if _try_rope_here(ty):
            return True
    print(f"[nav] 在 x≈{nx} 附近找不到繩索")
    return False


VERT_MAX_STEPS = 6      # 垂直移動最多幾層(防止在無解的地形上無限重試)

# ---- 卡在層與層之間的脫困 ----
# 【和「卡住看門狗」不是同一件事】看門狗管的是「位置完全不動」;這裡管的是角色停在
# 【不屬於任何平台的 y】—— 最典型是掛在繩索中段(實測 y=51,而平台只有 45 和 56)。
# 那個狀態下角色其實還會左右微動(x 在 18↔23 抖),位移大於 STUCK_TOL,看門狗判定
# 「有在動」永遠不觸發;而每段開頭的層校驗只會 break 去重規劃,重規劃出來的起點
# 一樣是錯的層 → 實測 REPLAN_MAX 用完、上層重挑點、再進來又是同一套,連續 30 幾輪
# 無限外圈直到人工停止。所以偵測到「不在任何層」時要先把角色弄回某個平台再談路徑。
OFFLAYER_DROPS = 3      # 歸位動作最多做幾次
OFFLAYER_LAND = 0.7     # 每次動作後等落地再讀位置(空中的讀數不可信,同 _unstick)


def _on_some_platform(p, platforms, margin=2):
    """角色是否真的站在某塊平台上(y 在容差內【而且 x 也在那塊平台的範圍內】)。

    【x 不能不管】原本只看 y,結果「y 接近某塊平台、但 x 差了幾十格」也算在層上 ——
    實測卡死案例:角色 (18,51),地圖有 y=53 的平台但 x 範圍是 44~62 與 85~124,
    角色根本不在上面,脫困卻因此不觸發,人就一直卡著。
    """
    if not p:
        return False
    return any(abs(p[1] - pf["y"]) <= Y_TOL
               and pf["xA"] - margin <= p[0] <= pf["xB"] + margin
               for pf in platforms)


def _deblock_layer(platforms):
    """把卡在層與層之間的角色弄回某個平台層。回是否回到層上。

    動作用【下跳】(按住 down + 跳躍鍵):掛在繩索上時這是唯一能脫離的輸入 —— 單按
    方向鍵會被繩索吃掉、單獨跳只會在繩上原地彈。落下去一定會踩到某塊平台,之後
    路徑重新規劃就有正確的起點了。

    刻意【不指定要回哪一層】:這裡只負責「回到可規劃的狀態」,回哪層由後續重規劃
    處理。硬指定目標層會退化成又一次 _go_vertical,而那正是卡住的地方。"""
    _release_move_keys()
    for i in range(OFFLAYER_DROPS):
        if _stop.is_set():
            return False
        p0 = _dot()
        print(f"[nav] 不在任何平台層(pos={p0}) → 下跳歸位(第 {i + 1} 次)")
        try:
            _keyboard.key_down("down")
            time.sleep(0.2)
            _press(JUMP_K1, hold=0.08)
        except Exception as e:
            print(f"[nav] 歸位動作失敗: {e!r}")
            return False
        finally:
            try:
                _keyboard.key_up("down")
            except Exception:
                pass
        time.sleep(OFFLAYER_LAND)
        p = _settle(retries=6)
        if _on_some_platform(p, platforms):
            print(f"[nav] 歸位成功 {p0} → {p}")
            _state["pos"] = list(p)
            return True
    print("[nav] 歸位失敗,仍不在任何平台層")
    return False


def _learn_blink_dy(span):
    """看到比 BLINK_DY_MAX 更大的單次瞬移跨距就把上限放寬。

    【為什麼要自己學】這個值決定建圖時「這個 x 上誰是最遠可達平台」,填小了會把真正
    的落點判成「超出範圍」,於是把中間那層誤認成最遠 —— 建出一條實機走不到的邊,角色
    在兩層之間來回,中間層整層失聯(實測 x≈27 的 y=45 就是這樣消失的)。
    而這個上限很難靠人工夾:要找到「一條 x 線上剛好有三層、距離跨過上限」的地形才測得出,
    平常量到的都只是「那兩層剛好差這麼多」,永遠是低估。既然實走時每次瞬移的跨距本來就
    在手上,超過就放寬,參數會自己收斂到真值。

    只放寬不收窄:跨距小可能只是那個 x 沒有更遠的平台,不代表上限變小。

    上限本身不寫回設定檔(那是職業參數,該由使用者確認後改),但印出來提示。"""
    global BLINK_DY_MAX
    if span <= BLINK_DY_MAX or span > BLINK_DY_MAX * 3:   # 3 倍以上當量測異常,不採信
        return
    old = BLINK_DY_MAX
    BLINK_DY_MAX = int(span)
    print(f"[nav] 實走瞬移跨距 {span} > 設定上限 {old} → 本次執行放寬為 {BLINK_DY_MAX}"
          f"(建議把職業設定的 blink_dy_max 一起改大,否則重開又會低估)")


def _blink_to_y(ty, skills=None):
    """瞬移職業的垂直移動:一層一層瞬移到目標層。回是否到達。

    【瞬移會越過較近的平台】落到 BLINK_DY_MAX 以內【最遠】的那個,不是停在最近的。
    所以某些 x 位置根本到不了中間層 —— 實測 x=98 從 y=49 往上會直接到 y=27,
    把 y=38 整個跳過;從 27 往下又直接回 49。pathgraph 已依這個規則建圖(只連最遠
    可達的平台),但執行時仍可能因量測誤差或地形差異繞回來,所以這裡再擋一次:
    看到走過的層又出現就放棄,交給上層換個 x 或重新規劃。
    沒有這道保護時實測會無限來回,90 秒只走到 6 個點。"""
    seen = []
    for _ in range(VERT_MAX_STEPS):
        if _stop.is_set() or _replan.is_set():
            return False
        p = _settle()
        if not p:
            return False
        _state["pos"] = list(p)
        dy = ty - p[1]
        if abs(dy) <= Y_TOL:
            return True
        if seen:
            _learn_blink_dy(abs(p[1] - seen[-1]))   # 上一次瞬移的實際跨距 → 校準上限
        if any(abs(p[1] - y) <= Y_TOL for y in seen):
            print(f"[nav] 垂直瞬移在 {seen + [p[1]]} 之間來回,到不了 {ty} → 放棄本段")
            return False
        seen.append(p[1])
        if not _blink_v(dy < 0, skills):
            print(f"[nav] 垂直瞬移走不動(y={p[1]} → 目標 {ty}),該方向可能沒有平台")
            return False
    return False


def _go_vertical(nx, ty, skills=None):
    """移到目標層。依職業選路:瞬移職業直接上下,二段跳職業要靠繩索/下跳。"""
    if MOVE_TYPE == "blink":
        return _blink_to_y(ty, skills)
    p = _dot()
    if p and ty > p[1]:
        _fall_to_y(ty, skills)
        return True
    return _rope_to(nx, ty)


def _merge_rope_chain(path):
    """把【連續的上繩段】併成一段。

    【為什麼要併】規劃器把上繩建模成「爬到相鄰的上一層」,所以跨兩層會排出
    68→56、56→45 兩段 rope。但實際物理不是這樣:_rope_up 按一次 C 就【一路衝到
    最頂層】(見它的 docstring),停不下來。照著兩段跑的結果(實測軌跡
    20260820-143616):

        按 C @68 → 衝到 21 → 下跳三次修正回 56(第一段目標)
        按 C @56 → 又衝到 28 → 下跳兩次回 45(第二段目標)

    而下跳修正的途中【就經過 45】—— 最終目標就在路上,卻先掉到 56 再爬一次。
    整整多了一次上繩 + 一趟修正,約 6 秒。

    併成一段之後:按一次 C 衝到頂,直接下跳修正到【最後那一段的目標層】,
    中間那些層根本不必停。水平位置取最後一段的 x(上繩點都在同一條繩索上)。
    """
    out = []
    i = 0
    while i < len(path):
        node, mt = path[i]
        if mt == "rope":
            j = i
            while j + 1 < len(path) and path[j + 1][1] == "rope":
                j += 1
            out.append((path[j][0], "rope"))     # 只留最後一段:C 反正會到頂
            i = j + 1
        else:
            out.append((node, mt))
            i += 1
    return out


def _goto_via_graph(tx, ty, points_dicts, platforms, precise=False, skills=None):
    """用平台重疊圖規劃到 (tx,ty),沿路徑分段執行按鍵流程(walk/jump/rope/fall)。
    skills:移動攻擊鍵(僅水平走位穿插;繩索/下跳/二段跳等垂直動作暫停,避免中斷)。

    【脫困後會重新規劃】看門狗把角色從卡點跳出來之後,角色已經不在原路徑的起點上了,
    沿用舊路徑等於照著過期的座標走(例如「從 x=26 走到 x=113」,但人已經被跳到 x=40)。
    所以 _replan 一旦被設起來就丟掉目前這條路徑,重讀位置、重算一條。"""
    for attempt in range(REPLAN_MAX):
        _replan.clear()
        p = _settle()
        if p is None:
            _state["error"] = "抓不到角色黃點"
            return False
        # 【規劃前先確認站在某層上】不在任何層(掛在繩索中段等)時 nearest_node 只會
        # 挑一個「最近」的錯誤起點,依它算出的每一段都對不上,重規劃幾次都是同一個錯。
        # 先把角色弄回平台,這條路徑才有意義。
        if not _on_some_platform(p, platforms):
            _state["phase"] = "deblock"
            nav_trace.event("deblock", note="不在任何平台層,先歸位再規劃")
            _deblock_layer(platforms)
            p = _settle() or p
        _state["pos"] = list(p)
        pts = [(int(d["x"]), int(d["y"])) for d in points_dicts]
        # jump=JUMP_DX:飛距是【職業參數】,不能讓 pathgraph 用它自己的預設 30,
        # 否則換職業後規劃出來的跳躍邊與實際飛得到的距離對不上。
        nodes, edges = pathgraph.build_physics(pts + [(int(tx), int(ty))], platforms,
                                              ropes=_ropes_fn() if _ropes_fn else None,
                                              jump=step_dx(),
                                              free_vertical=(MOVE_TYPE == "blink"),
                                              blink_dy=BLINK_DY_MAX,
                                              blink_up=BLINK_UP_MAX)
        start = pathgraph.nearest_node(nodes, p)
        # 【起點跨層 = 這條路徑從第一步就不可能成立】執行端的第一個檢查就是「現在
        # 的層是不是起點那一層」,跨層必定立刻失敗 → 重規劃 → 選到同一個節點 → 再
        # 失敗,三次之後放棄,角色永遠停在原地(實測:角色 (18,51),最近節點 (26,45),
        # 0.37 秒內三次 replan 然後卡死)。
        # 這種情況表示角色所在的層【沒有任何節點】—— 掛在繩索上、或站在沒被記錄
        # 巡邏點的平台上。先把它弄回有節點的層再說。
        if start and abs(start[1] - p[1]) > Y_TOL:
            print(f"[nav] 起點跨層(角色 y={p[1]},最近節點 y={start[1]}) → 先脫困歸位")
            _state["phase"] = "deblock"
            nav_trace.event("deblock", note=f"起點跨層 y={p[1]}→節點 y={start[1]}")
            _deblock_layer(platforms)
            p = _settle() or p
            _state["pos"] = list(p)
            start = pathgraph.nearest_node(nodes, p)
        path = pathgraph.shortest_path(edges, start, (int(tx), int(ty)))
        if path is None:
            _state["error"] = f"無路徑 {start}→({tx},{ty})"
            print(f"[nav] 無路徑 {start}→({tx},{ty})")
            return False
        path = _merge_rope_chain(path)
        _state["path"] = [[list(n), mt] for n, mt in path]
        print(f"[nav] 路徑{'(重規劃 %d)' % attempt if attempt else ''} "
              f"{start}→({tx},{ty}): {[(list(n), mt) for n, mt in path]}")
        prev_y = start[1]        # 規劃時角色所在的層;每段結束後更新成該段的目標層
        for node, mt in path:
            if _stop.is_set():
                return False
            if _replan.is_set():
                break                                   # 脫困過 → 這條路徑作廢,跳出去重算
            nx, ny = node
            # 【每段開始前確認還在預期的層】角色會意外掉層 —— 瞬移的落點可能剛好落在
            # 兩塊同高平台的間隙(實測 y=28 有 x20~57 與 x64~88 兩塊,間隙 57~64,
            # 一次 20px 的瞬移很容易停在裡面),角色就掉到下一層去了。
            # 不檢查的話會照著「以為還在原層」的路徑繼續走,後面每一段都是錯的。
            cur = _dot()
            if cur and abs(cur[1] - prev_y) > Y_TOL:
                print(f"[nav] 角色不在預期的層(y={cur[1]},應為 {prev_y}) → 重新規劃")
                # 【這一筆是「第一次錯、第二次對」的關鍵訊號】使用者回報的症狀正是
                # 這個:第一趟規劃走歪 → 重新規劃 → 第二趟才對。沒有記錄的話,事後
                # 完全看不出「這一趟到底有沒有重規劃過」。
                nav_trace.event("replan", note=f"y={cur[1]} 應為 {prev_y}")
                break
            _state["phase"] = "g_" + mt
            p_start = _dot()
            if mt == "walk":
                _move_to_x(nx, skills)                      # 同平台走位(移動攻擊穿插平A)
            elif mt in ("fall", "rope"):
                # 垂直移動。瞬移職業在任意 x 都能直接上下,先走到目標 x 再垂直即可;
                # 二段跳職業則是 fall=走到掉落點垂直落下、rope=走到繩索重疊區上繩。
                _move_to_x(nx, skills); _settle()
                _go_vertical(nx, ny, skills)
            elif mt == "jump":                              # 位移原語橫向飛到落點平台
                _settle()
                pp = _dot() or (nx, ny)
                dr = 1 if nx >= pp[0] else -1
                step = step_dx()
                cur = next((pf for pf in platforms if abs(pf["y"] - pp[1]) <= Y_TOL
                            and pf["xA"] - 2 <= pp[0] <= pf["xB"] + 2), None)
                tgt = next((pf for pf in platforms if abs(pf["y"] - ny) <= Y_TOL
                            and pf["xA"] - 3 <= nx <= pf["xB"] + 3), None)
                if cur and tgt:                             # 起跳點:使落點(起跳±step)落在目標平台內
                    if dr > 0:
                        lo, hi = (max(cur["xA"], tgt["xA"] - step),
                                  min(cur["xB"], tgt["xB"] - step))
                    else:
                        lo, hi = (max(cur["xA"], tgt["xA"] + step),
                                  min(cur["xB"], tgt["xB"] + step))
                    if lo <= hi:
                        _walk_to((lo + hi) // 2)            # 起跳點取範圍中間→落到目標平台中部(避邊緣)
                        _settle()
                _hop("right" if dr > 0 else "left", skills)
                _settle(); _move_to_x(nx, skills)           # 落點微調到目標 x
            p_end = _dot()
            _log_move(_state.get("mode", "move"), mt,
                      list(p_start) if p_start else None, [nx, ny],
                      list(p_end) if p_end else None)
            nav_trace.segment(mt, p_start, [nx, ny], p_end)
            prev_y = ny                                 # 這段的目標層,下一段據此校驗
        else:
            if not _replan.is_set():
                break                                   # 整條路徑跑完沒被打斷
            continue                                    # 脫困過 → 重新規劃
        # 走到這裡代表段迴圈被 break(掉層,或脫困)。掉層未必是壞事 —— 有時剛好掉到
        # 目標層上,那就不必再繞一圈重規劃。
        cur = _dot()
        if (not _replan.is_set() and cur
                and abs(cur[0] - tx) <= X_TOL and abs(cur[1] - ty) <= Y_TOL):
            break
    else:
        print(f"[nav] 重新規劃 {REPLAN_MAX} 次仍未走完,交給上層(巡邏會重挑點)")
        nav_trace.event("replan_exhausted", note=f"{REPLAN_MAX} 次仍未走完")
    _replan.clear()
    if precise:
        _state["phase"] = "fine_x"
        _fine_tune_x(tx)
    p = _settle()
    if p:
        _state["pos"] = list(p)
    arrived = bool(p and abs(p[0] - tx) <= X_TOL and abs(p[1] - ty) <= Y_TOL)
    _state["arrived"] = arrived
    return arrived


def _goto_sync(tx, ty, skills=None, precise=False):
    """同步導航到 (tx,ty),阻塞到完成。回是否到達。move_to 與巡邏循環共用。
    precise=True:水平照用二段跳/走路到容差內(可過衝),再走路回正到 ±PRECISE_X_TOL。

    【軌跡記錄掛在這裡,不是掛在 _run】_run 只有 move_to(手動導航/符文走位)在用,
    巡邏迴圈是【直接】呼叫 _goto_sync 的。掛錯地方的話巡邏整段都不會被記錄 ——
    實測就是這樣:巡邏跑了 18 分鐘,軌跡目錄只有符文那條路留下的幾筆。
    這裡是兩條路唯一的共同出口。"""
    _state.update({"target": [tx, ty], "arrived": False})
    nav_trace.start(_state.get("mode", "move"), (tx, ty))
    try:
        return _goto_sync_inner(tx, ty, skills, precise)
    finally:
        nav_trace.finish(_state.get("arrived"))


def _goto_sync_inner(tx, ty, skills=None, precise=False):
    terr = _terrain_fn() if _terrain_fn else None
    _state["terr_n"] = (-1 if terr is None else (len(terr[1]) if terr[1] else 0))
    if terr and terr[1]:                   # 有平台 → 用平台重疊圖規劃跨層路徑(按鍵流程)
        return _goto_via_graph(tx, ty, terr[0], terr[1], precise, skills)
    p = _settle()
    if p is None:
        _state["error"] = "抓不到角色黃點"
        return False
    _state["pos"] = list(p)
    dy = ty - p[1]
    if MOVE_TYPE == "blink":
        # 瞬移職業:垂直移動不挑 x,先走到目標 x 再一層層瞬移過去即可。
        if abs(dy) > Y_TOL:
            _state["phase"] = "pre_move_x"
            _move_to_x(tx, skills)
            _state["phase"] = "rope_up" if dy < 0 else "fall"
            _blink_to_y(ty, skills)
    elif dy < -Y_TOL:                      # 目標更高 → 【先水平對齊繩索(在目標 x 正下方),再上繩】
        _state["phase"] = "pre_move_x"     # 實測:繩索固定位置,原地按C不在繩索下方無效
        _move_to_x(tx, skills)             # 先走到目標 x(當前層),對齊繩索
        _state["phase"] = "rope_up"
        _rope_up(skills)                   # 上繩(升到頂層)
        p = _settle() or p
        if p and p[1] < ty - Y_TOL:        # 上繩過頭(頂層比目標高、y 更小)→ 下跳修正到目標層
            _state["phase"] = "fall_adjust"
            _fall_to_y(ty, skills)
    elif dy > Y_TOL:                       # 目標更低 → 下跳
        _state["phase"] = "fall"
        _fall_to_y(ty, skills)
    _state["phase"] = "move_x"
    _move_to_x(tx, skills)                 # 最後水平(同層直接走 / 上下層後微調)
    # 這條是【沒有平台資料時】的簡單導航,結構是線性的,沒有可以重規劃的路徑物件。
    # 脫困仍會讓上面的移動迴圈提早收手 → 這裡就會判定未到達,由上層(巡邏)重挑點再來。
    # 旗標一定要清掉,否則下一趟導航會一開始就自我中斷。
    _replan.clear()
    if precise:                            # 精確到位:二段跳可能過衝 → 走路回正到 ±1px
        _state["phase"] = "fine_x"
        _fine_tune_x(tx)
    p = _settle()                          # 統一:停穩(0抖動)後才判定到達,不用移動中瞬時值
    if p:
        _state["pos"] = list(p)
    # 到達判定用範圍容差(保證平A能施放);精確點的 ±1 已由 _fine_tune_x 盡量達成
    arrived = bool(p and abs(p[0] - tx) <= X_TOL and abs(p[1] - ty) <= Y_TOL)
    _state["arrived"] = arrived
    return arrived


def _run(tx, ty, skills, precise=False):
    try:
        _state.update({"phase": "settle", "steps": 0, "error": ""})
        if _focus_fn:
            try:
                _focus_fn()
            except Exception:
                pass
        _goto_sync(tx, ty, skills, precise=precise)
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
        _release_atk()                     # 放開按住的移動攻擊鍵
        _state["running"] = False


# ---- 路過就打:走位途中經過巡邏點,就地平A 再繼續 ----
# 原本只有【終點】會平A,路上經過的巡邏點都白走。對定點輸出的職業(不能邊移動邊攻擊)
# 差別很大 —— 那些點本來就是使用者挑的怪點,經過卻不打等於浪費一趟。
PASS_BY_TOL = 3        # 距離巡邏點多近算「經過」(小地圖 px,與 X_TOL 同量級)
_pass = {"points": [], "done": set(), "key": "", "mode": "hold2s", "on": False}


def set_pass_by(points, attack_key, cast_mode, enabled=True):
    """設定本輪「路過就打」的點。每輪開頭由 _patrol_run 呼叫,順便清掉上一輪的紀錄。"""
    _pass["points"] = [(int(p["x"]), int(p["y"])) for p in (points or [])]
    _pass["done"] = set()
    _pass["key"] = attack_key or ""
    _pass["mode"] = cast_mode or "hold2s"
    # 移動攻擊模式(mode="move")走位時本來就一直按著攻擊鍵,不需要這個機制
    _pass["on"] = bool(enabled and attack_key and cast_mode != "move")


def _maybe_cast_passing(pos):
    """走位途中檢查有沒有經過還沒打過的巡邏點。有就地平A 一次。回是否打了。

    【每個點一輪只打一次】用 done 記著 —— 否則角色在點附近來回微調時會反覆觸發,
    走位整個卡住。目標點本身不排除:它由到點流程再打一次,兩者不衝突(到點那次
    是站穩後打,這次是路過打)。"""
    if not _pass["on"] or not pos:
        return False
    for key in _pass["points"]:
        if key in _pass["done"]:
            continue
        if abs(pos[0] - key[0]) <= PASS_BY_TOL and abs(pos[1] - key[1]) <= PASS_BY_TOL:
            _pass["done"].add(key)
            _release_move_keys()           # 先停下來:定點職業不能邊移動邊攻擊
            _state["phase"] = "pass_cast"
            print(f"[nav] 路過巡邏點 {key} → 就地平A")
            _cast_skill(_pass["key"], _pass["mode"])
            _state["pass_n"] = _state.get("pass_n", 0) + 1
            return True
    return False


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
                        "face": p.get("face", ""),
                        "last": old["last"] if (old and old["skill"] == sk) else None}
    _place.clear()
    _place.update(new)


# ---- 已確認是「解不掉的符文」的紫標:暫時不因為它暫停巡邏 ----
# 紫標的語意是【符文 or 危險(玩家/特殊NPC進圖)】,分不出來時停巡邏是對的。但 rune
# 對某個紫標跑完整套解除流程(走到旁邊、開過謎題)之後,那個位置就【已知是符文】——
# 不是玩家。原本放棄後仍會讓巡邏每輪停下,人不重開就整晚停擺;而重開也沒用,兜底
# 立刻又停。所以只對「rune 親自放棄過、且位置吻合」的那一個紫標放行,其餘照舊立刻停。
_purple_ignore = {"pos": None, "until": 0.0, "tol": 3}


def ignore_purple_at(x, y, secs, tol=3):
    """把 (x,y) 的紫標標記成已知符文,secs 秒內不因為它暫停巡邏。rune 放棄時呼叫。"""
    _purple_ignore.update({"pos": (int(x), int(y)),
                           "until": time.monotonic() + float(secs), "tol": int(tol)})
    print(f"[nav] 紫標 ({x},{y}) 已確認是解不掉的符文 → {secs:.0f}s 內不因它暫停巡邏")


def clear_purple_ignore():
    """解除放行(符文解掉了/換地圖/使用者手動重設)。"""
    _purple_ignore.update({"pos": None, "until": 0.0})


def _purple_ignored(m):
    """這個紫標是不是正在放行中的那一個。"""
    gp = _purple_ignore.get("pos")
    if not gp or time.monotonic() >= _purple_ignore.get("until", 0.0):
        return False
    tol = _purple_ignore.get("tol", 3)
    return abs(m.get("x", 0) - gp[0]) <= tol and abs(m.get("y", 0) - gp[1]) <= tol


def _purple_present():
    """小地圖目前是否有【需要暫停巡邏】的紫標。抓不到狀態回 False。
    放行中的那個符文不算 —— 但只要另外還有紫標(可能是玩家)就照樣回 True。"""
    try:
        marks = minimap.status().get("purple") or []
        return any(not _purple_ignored(m) for m in marks)
    except Exception:
        return False


# ---- 到不了的巡邏點:退避停用 ----
# 【為什麼非有不可】放置技能點只要冷卻好就【無條件優先】(skip_ready[0]),而冷卻
# (rec["last"])只在成功放置後才更新 —— 到不了就永遠 ready、永遠被挑中。實測換地圖
# 後巡邏點對不上,同一個點連續 25~30 次「無路徑/未到達」,整趟巡邏鎖死到人工介入。
# 普通點靠「不連號」至少會換人,略過點連那層保護都沒有。
#
# 停用是【暫時】的:地形資料會隨著實走累積、地圖也可能是暫時被擋住,永久剔除等於
# 把使用者設好的點默默丟掉。連續失敗就把停用時間加倍(60→120→240s),真的永遠去
# 不了的點退避到 5 分鐘試一次,不再吃掉巡邏時間。
UNREACH_FAILS = 2        # 連續幾次到不了才停用(單次失敗可能只是路過被怪打斷)
UNREACH_COOLDOWN = 60.0  # 首次停用時長(秒)
UNREACH_MAX = 300.0      # 停用時長上限


def _patrol_run(points_fn, attack_key, cast_mode):
    """巡邏循環:【每輪重讀最新巡邏點】→ 運行中新增/修改放置技能、冷卻即時生效。
    巡邏點順序完全隨機、不連號(不會連續造訪同一點,如 111;例 1231/13231/1321231)。
    到點:① 先平A(確保落地站穩) ② 放置技能(冷卻已過才放,僅到點、只按該鍵、不移動)。
    紫標(特殊NPC/玩家進圖)出現 → 自動暫停巡邏(危險規避;另有即時 hook 兜兩道)。
    點若設 precise=True,到點會走路回正到 ±1px 才施放(定點放置技能)。"""
    visits = 0
    prev = None                            # 上一點座標 (x,y),用於不連號
    unreach = {}                           # (x,y) → {"n":連續失敗次數, "until":解禁時刻}
    while not _stop.is_set():
        if _patrol_time_up():              # 巡邏時限到 → 收工
            _state["phase"] = "time_up"
            _state["error"] = "巡邏時間到,已自動停止"
            print("[nav] 巡邏時間到 → 停止")
            return
        if _round_hook is not None:         # 死亡復活等每輪檢查;回 True 代表本輪別走位
            try:
                if _round_hook():
                    _state["phase"] = "hook_wait"
                    if _stop.wait(1.0):
                        return
                    continue
            except Exception as e:
                print(f"[nav] 每輪 hook 錯誤: {e!r}")
        if _purple_present():              # 紫標兜底:巡邏中/重開時紫標仍在 → 暫停
            _state["phase"] = "purple_pause"
            _state["error"] = "偵測到紫標,已自動暫停巡邏"
            print("[nav] 紫標存在 → 暫停巡邏")
            return
        # 召喚:冷卻一好就補,而且【先於走位】—— 對召喚流職業那是主要傷害來源,
        # 等走到巡邏點才放會白白空轉一段冷卻。
        # 解符文期間不會走到這裡:那條流程會先 stop 巡邏,走位改由 rune 自己的
        # move_to 負責,所以「符文出現時暫停召喚」是自然成立的,不需要額外開關。
        if _summon["priority"] and summon_ready():
            _state["phase"] = "summon"
            _cast_summon()
            if _stop.is_set():
                return
        pts = points_fn() or []            # 每輪重讀:運行中改放置技能/冷卻即時生效
        if not pts:
            if _stop.wait(0.5):
                return
            continue
        _refresh_places(pts)
        now = time.monotonic()
        # 略過放置技能點:冷卻好→優先去放;冷卻中→排除(只在普通點循環刷怪)
        # 停用中(到不了)的點兩類都先濾掉,免得又被挑中空跑一趟。
        skip_ready, normal = [], []
        for p in pts:
            r = unreach.get((p["x"], p["y"]))
            if r and now < r["until"]:
                continue
            if p.get("skill") and p.get("skip"):
                rec = _place.get((p["x"], p["y"]))
                if rec and (rec["last"] is None or now - rec["last"] >= rec["cd"]):
                    skip_ready.append(p)
            else:
                normal.append(p)
        # 全部點都在停用中 → 不能就這樣空轉下去(整張圖被擋住、或地形資料一時不準都
        # 可能全滅)。全解禁重來一輪,寧可再試一次也不要巡邏靜止不動。
        if not skip_ready and not normal and unreach:
            print("[nav] 所有巡邏點都在停用中 → 全部解禁重試")
            unreach.clear()
            continue
        if skip_ready:
            pt = skip_ready[0]                                   # 冷卻好的放置技能點→優先導航
        elif normal:
            choices = [p for p in normal if (p["x"], p["y"]) != prev]   # 普通點不連號循環
            pt = random.choice(choices) if choices else normal[0]
        else:
            if _stop.wait(1.0):                                  # 只剩冷卻中的略過點→等待
                return
            continue
        prev = (pt["x"], pt["y"])
        tx, ty = pt["x"], pt["y"]
        _state["phase"] = "goto"
        # 路過就打:本輪走位途中經過的巡邏點都就地平A 一次。每輪重設,所以
        # 「這一趟打過的點」不會延續到下一趟。
        set_pass_by(pts, attack_key, cast_mode)
        atk_skills = [attack_key] if cast_mode == "move" else None   # 移動攻擊:走位穿插平A
        arrived = _goto_sync(tx, ty, skills=atk_skills, precise=bool(pt.get("precise")))
        if _stop.is_set():
            return
        _release_move_keys()               # 到點靜止:先放開移動鍵,平A/放置技能時皆不移動
        if not arrived:                    # 沒到點附近(導航受阻)→ 不施放平A/放置技能,重挑點重試
            _state["phase"] = "retry"
            r = unreach.setdefault((tx, ty), {"n": 0, "until": 0.0, "cd": UNREACH_COOLDOWN})
            r["n"] += 1
            msg = ""
            if r["n"] >= UNREACH_FAILS:    # 連續到不了 → 停用一段時間,別再讓它吃巡邏時間
                r["until"] = time.monotonic() + r["cd"]
                msg = f",連續 {r['n']} 次到不了 → 停用 {r['cd']:.0f}s"
                r["cd"] = min(UNREACH_MAX, r["cd"] * 2)   # 再失敗就退避得更久
            print(f"[nav] 未到達 ({tx},{ty}) pos={_state.get('pos')},跳過施放、重試{msg}")
            if _stop.wait(0.3):
                return
            continue
        unreach.pop((tx, ty), None)        # 到得了就把失敗紀錄清乾淨,下次不帶著舊帳
        # ① 平A:移動攻擊模式已在走位中穿插,到點不再cast;其他模式到點施放(確保站穩)
        if cast_mode != "move":
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
                _face_for_place(pt.get("face"))
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
        _release_atk()                     # 放開按住的移動攻擊鍵
        _state["running"] = False
        nav_trace.finish(_state.get("arrived"))


def move_to(tx, ty, skills=None, precise=False):
    """啟動背景導航到 (tx,ty)。skills=移動時同時按的技能鍵列表。回 (ok,msg)。

    precise=True:到位後再走路回正到 ±PRECISE_X_TOL(1px)。

    【什麼時候該開】呼叫端對落點的要求比 X_TOL(3) 還嚴的時候。最典型的是符文:
    它要角色停在離紫標 5~7 格的環內(環寬 3 格),而非精確模式的落點會在 3~9 格
    之間浮動 —— 誤差範圍比環還寬,落在環外是常態。太近會蓋住紫標(驗證失效)、
    太遠按不到啟動鍵(整輪白費並吃掉符文冷卻),兩種都得再跑一次完整導航補救,
    每次 3~7 秒。先花 ~1 秒走準,比事後補救便宜得多。"""
    global _thread
    with _op_lock:
        if _state["running"]:
            return False, "導航進行中"
        if _keyboard is None:
            return False, "鍵盤未連線"
        _stop.clear()
        _state.update({"running": True, "phase": "starting", "target": [tx, ty],
                       "arrived": False, "error": ""})
        _thread = threading.Thread(target=_run,
                                   args=(int(tx), int(ty), skills or [], bool(precise)),
                                   daemon=True)
        _thread.start()
        _stuck_start()
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
        if _patrol_time_up():          # 時限已到就別再開(解符文後接回巡邏也走這裡)
            return False, "巡邏時間已到"
        _stop.clear()
        _place.clear()
        _state.update({"running": True, "phase": "patrol", "mode": "patrol",
                       "arrived": False, "error": "", "rounds": 0,
                       "stuck_n": 0, "stuck_ok": 0, "stuck_last": None})
        _thread = threading.Thread(target=_patrol_wrap,
                                   args=(points_fn, attack_key, cast_mode), daemon=True)
        _thread.start()
        _stuck_start()
        global _timer_thread, _timer_gen
        _timer_gen += 1                    # 讓先前殘留的看門狗退場
        if _patrol_deadline is not None:
            _timer_thread = threading.Thread(target=_patrol_timer,
                                             args=(_timer_gen,), daemon=True)
            _timer_thread.start()
        return True, "ok"


def stop(user=False):
    """停止導航/巡邏。

    user=True 代表【使用者主動要求停止】(中控頁的停止鍵、Arduino 紅色按鈕),
    這會另外豎起一支旗子。

    【為什麼要區分】解符文流程一開始就會呼叫 stop() 把巡邏停掉,好自己接管走位;
    它接著跑一個最多 3 輪、每輪含 8 秒等待的重試迴圈,中途還會再叫 move_to()。
    使用者這時按停止,只停得掉當下那條導航執行緒 —— rune 的迴圈沒在看,下一輪
    又把導航開起來,最後收場時甚至會接回巡邏,等於把使用者的停止推翻。
    有了這支旗子,rune 才分得出「是我自己要停」還是「人要停」,後者立刻收手。"""
    if user:
        _user_stop.set()
    _stop.set()
    t = _thread
    if t and t is not threading.current_thread():
        t.join(timeout=3.0)
    _state["running"] = False
    return True


def user_stopping():
    """使用者是否要求停止(且還沒重新啟動)。長流程(解符文)每個等待點都該看它。"""
    return _user_stop.is_set()


def clear_user_stop():
    """使用者重新啟動巡邏/導航時清掉。只由「使用者發起」的入口呼叫 ——
    rune 內部的 move_to 不能清,否則它一走位就把人按下的停止抹掉。"""
    _user_stop.clear()


def pause_purple():
    """紫標偵測 hook 呼叫:標記原因後停止巡邏/導航(危險規避)。
    若由巡邏執行緒自身觸發(_dot→detect→hook),stop() 只 set 事件、不 join 自己→安全。

    放行中的符文(rune 解不掉、已確認不是玩家)不觸發暫停 —— 否則 rune 一放棄,hook
    這條路徑就把巡邏停掉,和巡邏兜底一起把人鎖在原地。場上若另有紫標仍照常暫停。"""
    if not _purple_present():
        return
    if _state.get("running"):
        _state["error"] = "偵測到紫標,已自動暫停巡邏"
        _state["phase"] = "purple_pause"
        print("[nav] 紫標 hook → 暫停巡邏")
    stop()
