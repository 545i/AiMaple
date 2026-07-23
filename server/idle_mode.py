# -*- coding: utf-8 -*-
"""閒置(掛機)模式：維持角色活動的隨機化自動輸入。

用途：楓之谷「放置輪迴」等 buff 需定期重放,且遊戲不允許站原地一直施放,
還會掃「固定頻率」的輸入判機器人。故本模式以【完全隨機的時序】穿插
左右移動(不站原地)與施放技能,經 Arduino HID 送出(硬體訊號)。

限制(由 main.py 強制)：僅中控(主人 token)可操作,且與訪客(出租)模式互斥。

注意：這是對「自己遊戲帳號」的自動化。多數線上遊戲條款禁止任何自動化/掛機,
封號風險自負(README 已提醒)。
"""
import math
import random
import threading
import time

from config import (IDLE_SKILL_KEY, IDLE_MOVE_KEYS, IDLE_MOVE_MIN, IDLE_MOVE_MAX,
                    IDLE_GAP_MIN, IDLE_GAP_MAX, IDLE_SKILL_MIN, IDLE_SKILL_MAX,
                    IDLE_KEY_GAP_MIN, IDLE_KEY_GAP_MAX)

_keyboard = None
_focus_fn = None              # 施放技能前呼叫,確保 MapleStory 在前景(輸入才進得去)
_thread = None
_stop = threading.Event()
_running = False
_started_at = 0.0
_op_lock = threading.Lock()   # 序列化 start/stop,防快速連點造成雙重啟動/旗標交錯
# 按鍵追蹤(供中控頁監視「桌面寵物式亮燈」+ 下一次技能倒數):
_events = {}                  # key -> 最近觸發的 monotonic 時間
_next_skill_at = 0.0         # 下一次施放技能的 monotonic 時間


def _note(key):
    """記錄某鍵剛被送出(供前端亮燈)。"""
    _events[key] = time.monotonic()


def set_focus_fn(fn):
    """注入焦點守衛函式(main.py 傳 enforce_focus)。避免 idle_mode 直接依賴
    video_pipeline 造成循環 import。"""
    global _focus_fn
    _focus_fn = fn


def set_keyboard(kb):
    global _keyboard
    _keyboard = kb


def is_running():
    return _running


def status():
    now = time.monotonic()
    keys = {}
    for k in list(IDLE_MOVE_KEYS) + [IDLE_SKILL_KEY]:
        ts = _events.get(k, 0.0)
        keys[k] = round(now - ts, 2) if ts else 999.0   # 距今幾秒觸發(前端<0.6 亮燈)
    return {"running": _running,
            "uptime": int(time.time() - _started_at) if _running else 0,
            "keys": keys,
            "next_skill_in": max(0, round(_next_skill_at - now)) if (_running and _next_skill_at) else 0}


def _lognorm(median, sigma, lo, hi):
    """對數常態延遲(擬人)。人類反應/操作間隔非均勻分布,而是右偏——多數偏短、
    偶爾長尾。均勻分布(uniform)的平坦直方圖與硬邊界易被統計分析(分布擬合/
    自相關)識破;對數常態的形狀更接近真人。median=中位數,sigma 越大越發散。"""
    v = median * math.exp(random.gauss(0.0, sigma))
    return max(lo, min(hi, v))


def _wait(a, b):
    return _stop.wait(random.uniform(a, b))


def _move_once():
    """按住隨機方向鍵一段(對數常態)時間再放開。回 True 表示中途被要求停止。"""
    mk = random.choice(IDLE_MOVE_KEYS)
    dur = _lognorm(0.55, 0.55, IDLE_MOVE_MIN, IDLE_MOVE_MAX)
    _keyboard.key_down(mk)
    _note(mk)                       # 亮燈追蹤
    stopped = _stop.wait(dur)
    _keyboard.key_up(mk)
    return stopped


def _key_gap():
    """按鍵之間的最小間隔(放開→下次按下),讓遊戲分辨成獨立按鍵事件。回 True=停止。"""
    return _stop.wait(_lognorm((IDLE_KEY_GAP_MIN + IDLE_KEY_GAP_MAX) / 2.0, 0.4,
                               IDLE_KEY_GAP_MIN, IDLE_KEY_GAP_MAX))


def _idle_gap():
    """動作間停頓:多數短、偶爾(約 1/8,擬人分心)長停。皆對數常態,回 True=停止。"""
    if random.random() < 0.12:
        d = _lognorm(9.0, 0.7, 4.0, 45.0)                     # 分心長停
    else:
        d = _lognorm(1.0, 0.7, IDLE_GAP_MIN, IDLE_GAP_MAX * 2)  # 一般停頓
    return _stop.wait(d)


def _loop():
    global _next_skill_at
    # 首次很快施放一次(暖身移動幾秒後),確保 buff 立即生效、不空窗;
    # 之後每次都重設為 35~95 秒(IDLE_SKILL_MIN~MAX)隨機。
    next_skill = time.monotonic() + random.uniform(8, 20)
    _next_skill_at = next_skill
    # 叢發狀態(burstiness):真人時而連續操作、時而停手。用一個會變的
    # 「本輪連續移動次數」取代固定機率,讓動作序列的節奏也不規律。
    burst = random.randint(1, 4)
    try:
        while not _stop.is_set():
            if time.monotonic() >= next_skill:
                # 關鍵動作(施放 buff)前先確保 MapleStory 在前景,否則按鍵送到別的
                # 視窗、或遊戲沒焦點收不到 → buff 放不出來。焦點守衛有 2 秒節流。
                if _focus_fn:
                    try:
                        _focus_fn()
                    except Exception:
                        pass
                # 施放前先移動一下,避免「站定點固定施放」的可辨識模式
                if _move_once():
                    break
                if _key_gap():                      # 移動放開→技能按下 的間隔
                    break
                # 技能(放置輪迴)用韌體 tap(裸 token 4):韌體內建 delay(20) 確保遊戲
                # 讀到,與訪客模式同一條驗證有效的路徑。數字鍵走 DOWN:/UP: 實機無效,
                # 故不用 key_down/key_up。
                _keyboard.tap(IDLE_SKILL_KEY)
                _note(IDLE_SKILL_KEY)               # 亮燈追蹤
                next_skill = time.monotonic() + random.uniform(IDLE_SKILL_MIN, IDLE_SKILL_MAX)
                _next_skill_at = next_skill
                if _idle_gap():
                    break
                burst = random.randint(1, 4)
                continue
            # 一輪叢發:連續移動 burst 次(每次時長各自隨機),每次移動之間留按鍵間隔。
            for _ in range(burst):
                if _stop.is_set() or _move_once():
                    break
                if _key_gap():                      # 兩次移動之間的最小間隔
                    break
            if _idle_gap():
                break
            burst = random.randint(1, 4)
    finally:
        # 停止/例外：放開所有可能按著的移動鍵,避免卡住角色一直走
        for k in IDLE_MOVE_KEYS:
            try:
                _keyboard.key_up(k)
            except Exception:
                pass


def start():
    """啟動掛機執行緒。已在跑或沒有鍵盤則不動作。回傳是否運作中。"""
    global _thread, _running, _started_at
    with _op_lock:
        if _running or _keyboard is None:
            return _running
        _stop.clear()
        _running = True
        _started_at = time.time()
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
        print("[idle] 閒置掛機模式啟動")
        return True


def stop():
    """停止掛機並等執行緒收尾(放開按鍵)。"""
    global _running, _thread
    with _op_lock:
        if not _running:
            return False
        _stop.set()
        _running = False
        t = _thread
        _thread = None
    # join 在鎖外,避免與 _loop 可能的收尾互卡;_loop 本身不取 _op_lock
    if t and t is not threading.current_thread():
        t.join(timeout=2.0)
    print("[idle] 閒置掛機模式停止")
    return True
