# -*- coding: utf-8 -*-
"""旋轉符文即時預覽的累積觀測狀態機。

【只做累積與狀態機,判定邏輯全部呼叫 rune_wheel】這裡不重新實作角速度反轉/
同步假影/圓形平均/靜止旋轉分類 —— 全部呼叫既有的 `rune_wheel` 公開函式與
`_circular_r`/`STATIC_R_MIN`/`STATIC_MIN_SAMPLES`(這幾個雖然帶底線,但
`tests/test_rune_wheel.py` 本身就直接拿來用,屬於模組事實上的穩定介面)。
`rune_wheel.solve_from_angles()` 是「全部四支都判得出來才回答案,任一支沒判出
來就回 None」的一次性用法,不適合這裡的「逐支各自顯示目前最好的判定,判不出來
就顯示觀察中」——所以這裡另外寫一層薄的逐支狀態機,邏輯步驟跟
`solve_from_angles` 完全對齊(靜止判斷、旋轉走 `find_wobbles`、跨支
`drop_synced_wobbles` 過濾同步假影),只是不要求「全部到齊才回答」。

【為什麼需要跨呼叫累積】即時預覽一次 HTTP 呼叫只能抓半秒左右的畫面(見
`_capture_burst_angles`),旋轉一圈要 0.9 秒、晃動偵測抓穩要 1~3 秒 ——
單次呼叫的樣本不夠。所以維護一個模組層級的滾動緩衝,呼叫端(`rune_viz`)
每次呼叫 `observe()` 把新抓到的一小段幀餵進來,這裡負責累積、判斷「這批新
樣本是不是同一顆符文的延續」,不是就整批重置。

緩衝重置門檻(理由見各處註解):
    STALE_SECS  = 3.0   兩次呼叫之間隔太久,不能假設中間符文沒換。使用者的
                        UI 每 0.5~1 秒才會發一次請求,3 秒是好幾個輪詢週期,
                        正常使用不會誤觸;真的隔了 3 秒代表使用者切走分頁/
                        關掉預覽又重開/符文謎題已經結束又重新出現。
    BOX_JUMP_PX = 25.0  兩次呼叫拿到的框中心位移超過這個值 → 視為不同一顆
                        符文(或畫面整個變了)。手上驗證用的框大約 30px 寬
                        (見 tests/test_rune_wheel_integration.py 的合成框),
                        取半個框寬當門檻:小於它算偵測雜訊,大於它代表框
                        跳到別的位置去了。
    偵測不到 4 支 / 沒有遊戲畫面 → 直接重置(見 `ingest(None, ...)`)。

frame_idx 的跨呼叫間隔刻意 >1(見 `FRAME_GAP`):`rune_wheel.find_wobbles`
只在幀號連續(差 1)時才計算角速度,兩次 HTTP 呼叫之間的真實時間間隔未知
(使用者輪詢的節奏、網路延遲都會讓它變化),硬算成連續幀只會生出假的角速度
尖峰、誤觸發晃動偵測——這正是 `find_wobbles` 本來就有的「中間缺幀,角速度
算出來沒有意義,跳過」設計,這裡刻意讓呼叫之間的邊界被當成缺幀。
"""
import math
import threading
import time

import rune_wheel

STALE_SECS = 3.0
BOX_JUMP_PX = 25.0
BURST_SECS = 0.5
BURST_POLL_GAP = 0.01
FRAME_GAP = 2                  # 只需要 != 1,讓呼叫邊界被 find_wobbles 當成缺幀
MAX_SAMPLES = 3000              # 緩衝上限(防止使用者長開預覽時無限成長,見檔頭)

# 【比 rune_wheel.STATIC_MIN_SAMPLES(=3)更保守的門檻,實測踩過的坑】用
# tools/eval_rune_wheel.py 的驗證影片跑累積收斂(見 .superpowers/live-auto.md)
# 時實測到:影片某段色遮罩幾乎讀不出角度(angle_of 大量回 None),直到剛好
# 累積到 3 個有效樣本、彼此又碰巧接近,圓形集中度 R 就衝到 0.9+,被
# STATIC_R_MIN 判成「靜止」——答案是錯的,那支箭頭其實在轉,只是多數幀讀
# 不到顏色。3 個樣本作為「這是這支箭頭全部的觀測」時沒問題(rune_wheel 自己
# 的離線測試就是這樣用),但在這裡樣本是【持續累積】的,3 個樣本、其餘幾乎
# 全是 None,代表訊號本身不可靠,不該就地拍板。這裡在真的呼叫 rune_wheel 的
# 分類邏輯前多加一層更保守的樣本數門檻,樣本不足前一律留在「觀察中」。
MIN_SAMPLES_FOR_CLASSIFY = 8

_lock = threading.Lock()
_state = {
    "frame_idxs": [],
    "angles": [[], [], [], []],
    "boxes_ref": None,
    "last_ts": None,
    "session_start_ts": None,
}
_last_status = None


def _empty_arrow():
    return {"motion": "unknown", "settled": False, "direction": None,
            "angle": None, "n_samples": 0, "n_wobbles": 0}


def _reset_locked():
    _state["frame_idxs"] = []
    _state["angles"] = [[], [], [], []]
    _state["boxes_ref"] = None
    _state["last_ts"] = None
    _state["session_start_ts"] = None


def reset():
    """完全清空累積緩衝與快取狀態(供呼叫端/測試手動重置)。"""
    global _last_status
    with _lock:
        _reset_locked()
        _last_status = None


def _center(box):
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _max_center_jump(boxes_a, boxes_b):
    best = 0.0
    for a, b in zip(boxes_a, boxes_b):
        ax, ay = _center(a)
        bx, by = _center(b)
        best = max(best, math.hypot(ax - bx, ay - by))
    return best


def _trim_locked():
    n = len(_state["frame_idxs"])
    if n > MAX_SAMPLES:
        cut = n - MAX_SAMPLES
        _state["frame_idxs"] = _state["frame_idxs"][cut:]
        for i in range(4):
            _state["angles"][i] = _state["angles"][i][cut:]


def ingest(boxes4, per_arrow_new_angles, reason="", now=None, gap=None):
    """把一批新樣本併入累積緩衝,回傳目前(累積後)的逐支狀態。

    boxes4:這次呼叫偵測到的 4 支框(整幀座標,左到右),或 None(沒偵測到)。
    per_arrow_new_angles:長度 4 的 list,每項是這批新幀的角度序列(可含
    None)。boxes4 是 None 時這個參數不使用。
    reason:boxes4 為 None 時的原因(給 /rune/live/info 用,例如 "no_frame"/
    "no_model"/"no_boxes"),回傳狀態會原樣帶出去。
    now:時間戳(monotonic 秒),測試用來注入固定值;不傳就用目前時間。
    gap:這批新樣本與上一批之間的幀號間隔。預設 FRAME_GAP(=2),代表「中間
        缺幀」——那是 per-request 連拍時代的正確語意(兩次 HTTP 呼叫之間隔多久
        未知)。常駐背景迴圈是【連續】取格的,它會傳 gap=1,讓 find_wobbles
        真的算得出跨批的角速度;迴圈自己漏掉一格時再傳 FRAME_GAP。
    """
    now = time.monotonic() if now is None else now
    with _lock:
        if boxes4 is None or len(boxes4) != 4:
            _reset_locked()
            return _status_locked(now, reset=True, n_boxes=0, reason=reason or "no_boxes")

        stale = _state["last_ts"] is not None and (now - _state["last_ts"]) > STALE_SECS
        jumped = (_state["boxes_ref"] is not None
                  and _max_center_jump(_state["boxes_ref"], boxes4) > BOX_JUMP_PX)
        did_reset = stale or jumped
        if did_reset:
            _reset_locked()

        if _state["session_start_ts"] is None:
            _state["session_start_ts"] = now
        _state["boxes_ref"] = boxes4
        _state["last_ts"] = now

        if per_arrow_new_angles and len(per_arrow_new_angles[0]):
            n_new = len(per_arrow_new_angles[0])
            step = FRAME_GAP if gap is None else gap
            start_idx = (_state["frame_idxs"][-1] + step) if _state["frame_idxs"] else 0
            _state["frame_idxs"].extend(start_idx + k for k in range(n_new))
            for i in range(4):
                _state["angles"][i].extend(per_arrow_new_angles[i])
            _trim_locked()

        return _status_locked(now, reset=did_reset, n_boxes=4, reason=reason)


def _status_locked(now, reset, n_boxes, reason):
    if n_boxes == 0:
        return {
            "reason": reason,
            "n_boxes_detected": 0,
            "arrows": [_empty_arrow() for _ in range(4)],
            "all_settled": False,
            "dirs": None,
            "reset": reset,
            "session_age_sec": 0.0,
            "n_frames": 0,
        }

    frame_idxs = _state["frame_idxs"]
    per_raw = []          # (motion, valid_angles, n_samples)
    wobble_inputs = []    # 只有 rotating 的那項非空,其餘是 []（比照 solve_from_angles）
    for i in range(4):
        angs = _state["angles"][i]
        valid = [a for a in angs if a is not None]
        n = len(valid)
        if n < MIN_SAMPLES_FOR_CLASSIFY:
            per_raw.append(("unknown", valid, n))
            wobble_inputs.append([])
            continue
        if rune_wheel._circular_r(valid) >= rune_wheel.STATIC_R_MIN:
            per_raw.append(("static", valid, n))
            wobble_inputs.append([])
        else:
            per_raw.append(("rotating", valid, n))
            wobble_inputs.append(rune_wheel.find_wobbles(angs, frame_idxs))

    filtered = rune_wheel.drop_synced_wobbles(wobble_inputs)

    arrows = []
    dirs = []
    n_settled = 0
    for i in range(4):
        motion, valid, n = per_raw[i]
        last_angle = valid[-1] if valid else None
        if motion == "unknown":
            a = {"motion": "unknown", "settled": False, "direction": None,
                 "angle": last_angle, "n_samples": n, "n_wobbles": 0}
        elif motion == "static":
            mean_a = rune_wheel.circ_mean(valid)
            d = rune_wheel.nearest_cardinal(mean_a) if mean_a is not None else None
            a = {"motion": "static", "settled": d is not None, "direction": d,
                 "angle": mean_a, "n_samples": n, "n_wobbles": 0}
        else:
            fw = filtered[i]
            d = None
            if fw:
                mean_a = rune_wheel.circ_mean([ang for _f, ang in fw])
                d = rune_wheel.nearest_cardinal(mean_a) if mean_a is not None else None
            a = {"motion": "rotating", "settled": d is not None, "direction": d,
                 "angle": last_angle, "n_samples": n, "n_wobbles": len(fw)}
        arrows.append(a)
        dirs.append(a["direction"])
        n_settled += int(a["settled"])

    all_settled = n_settled == 4
    return {
        "reason": reason,
        "n_boxes_detected": 4,
        "arrows": arrows,
        "all_settled": all_settled,
        "dirs": dirs if all_settled else None,
        "reset": reset,
        "session_age_sec": round(now - _state["session_start_ts"], 2),
        "n_frames": len(frame_idxs),
    }


def _capture_burst_angles(frame0, boxes4):
    """全速連拍約 BURST_SECS 秒,回這批新幀裡每支箭頭的角度序列(長度與幀數
    相同,可能含 None)。frame0 是呼叫端已經抓到的第一幀(用它就不用再多等
    一輪擷取)。

    request_full_rate 是引用計數(見 wgc.py),用 try/finally 保證還原——
    不還原的話擷取會一直全速跑,吃掉一顆核心 66% 的代價賴著不走(這是
    server/rune.py::_solve_wheel 已經踩過、記在 wgc.py 註解裡的坑,這裡照抄
    同一個防護模式)。"""
    import minimap
    import wgc

    frames = [frame0]
    wgc.request_full_rate(True)
    try:
        t_end = time.monotonic() + BURST_SECS
        while time.monotonic() < t_end:
            time.sleep(BURST_POLL_GAP)
            f = minimap._grab_window()
            if f is not None:
                frames.append(f)
    finally:
        wgc.request_full_rate(False)

    per_arrow = [[] for _ in range(4)]
    for f in frames:
        for i, box in enumerate(boxes4):
            x0, y0, x1, y1 = (int(round(v)) for v in box)
            crop = f[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            per_arrow[i].append(rune_wheel.angle_of(crop))
    return per_arrow


def observe(frame0, boxes4, reason="", now=None):
    """一次 `/rune/live` 呼叫的完整流程:有框就全速連拍一小段、把角度併入
    緩衝;沒框(沒偵測到/沒遊戲畫面/模型不可用)就直接重置。回傳的狀態同時
    快取起來,給 `get_last_status()`(`/rune/live/info`)讀,不用再擷取一次。"""
    global _last_status
    if frame0 is None or boxes4 is None or len(boxes4) != 4:
        status = ingest(None, None, reason=reason or "no_boxes", now=now)
    else:
        angles = _capture_burst_angles(frame0, boxes4)
        status = ingest(boxes4, angles, reason=reason, now=now)
    with _lock:
        _last_status = status
    return status


def get_last_status():
    """最近一次 `observe()` 的結果(不觸發新擷取)。從未呼叫過時回一個
    「尚未開始」的預設值,結構跟正常回傳一致,前端不用另外判斷欄位缺不缺。"""
    with _lock:
        if _last_status is not None:
            return _last_status
    return {
        "reason": "not_started",
        "n_boxes_detected": 0,
        "arrows": [_empty_arrow() for _ in range(4)],
        "all_settled": False,
        "dirs": None,
        "reset": False,
        "session_age_sec": 0.0,
        "n_frames": 0,
    }


# ==========================================================================
# 常駐背景迴圈 —— 疊圖(overlay)的資料來源
# ==========================================================================
# 【為什麼要從「每次 HTTP 呼叫連拍 0.5 秒」改成常駐迴圈】舊做法實測每次
# /rune/live 要 688~734ms(其中幾乎全是那 0.5 秒連拍),加上前端 150ms 間隔,
# 整體只有約 1.2 fps;而且每次呼叫是一段孤島,島與島之間刻意用 FRAME_GAP=2
# 標成缺幀,find_wobbles 只算連續幀 —— 等於每 0.5 秒就把跨批的角速度全部丟掉。
# 常駐迴圈兩個問題一起解:畫面連續(疊在 30fps 的遠端影像上),樣本也連續。
#
# 【成本分配:角度每幀算,定位每 LOOP_DETR_EVERY 幀才算】
#   角度  4 個小裁切做 HSV 重心,次毫秒等級 → 每一幀都算,不然角速度會斷
#   定位  RT-DETR 一次約 28ms → 太貴。但箭頭是【原地】旋轉、框幾乎不動,
#         5Hz 更新綽綽有餘(見 rune.py::ROTATE_BOX_JITTER 的同一個觀察)
#
# 【誰啟動它】沒有開關端點:`get_overlay()` 被讀到就會啟動,超過 LOOP_IDLE_STOP
# 秒沒人讀就自己停。前端只要「開著就一直輪詢、關掉就不輪詢」,不必記得關 ——
# 忘了關的代價是全速擷取一直開著(約一顆核心 66%,見 wgc.py)。
LOOP_DETR_EVERY = 6            # 每幾幀跑一次 RT-DETR 定位(其餘幀只算角度)
LOOP_IDLE_STOP = 3.0           # 沒人讀 overlay 超過這麼久就自動停
LOOP_POLL_GAP = 0.004          # 輪詢新影格的間隔上限(比 30fps 密,避免漏格)
OVERLAY_MIN_SCORE = 0.5        # 疊圖只畫這個信心以上的候選框(使用者指定)

_loop_thread = None
_loop_lock = threading.Lock()
_loop_last_read = 0.0
_overlay = {"reason": "not_started", "boxes": [], "frame": None,
            "fps": 0.0, "is_window": False, "n_frames": 0}


def _norm_box(box, w, h):
    """整幀像素座標 → 正規化 0~1,並【夾在邊界內】。

    夾邊界不是形式主義:框是模型輸出的,可能超出畫面(尤其箭頭貼在搜尋帶邊緣
    時);前端拿它乘上影像矩形就會畫到影像外面去,疊在遠端畫面上看起來像是
    偵測跑到遊戲畫面之外。完全落在畫面外的框直接丟掉(回 None)。"""
    x0, y0, x1, y1 = (float(v) for v in box)
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return None
    return [max(0.0, x0 / w), max(0.0, y0 / h), min(1.0, x1 / w), min(1.0, y1 / h)]


def _loop_body():
    """背景迴圈本體。任何例外都不能讓執行緒無聲死掉 —— 記進 overlay 的 reason,
    前端看得到。"""
    global _overlay
    import minimap
    import rune_detr
    import video_pipeline
    import wgc
    # 候選框(含信心分數)走 rune_viz 已經接好的那條路 —— 它負責把 tools/ 放進
    # sys.path,這裡不再重複一份路徑設定。拿不到就只畫選中的 4 支,不整條掛掉。
    try:
        import rune_viz
        viz_rune_detect = rune_viz.viz_rune_detect
    except Exception:
        viz_rune_detect = None

    wgc.request_full_rate(True)
    try:
        i = 0
        prev_ts = None
        had_prev = False               # 上一輪有沒有成功併入樣本(決定 gap 給 1 還是缺幀)
        boxes4 = None
        cands = []
        fps_t0 = time.monotonic()
        fps_n = 0
        fps = 0.0
        while True:
            with _loop_lock:
                if time.monotonic() - _loop_last_read > LOOP_IDLE_STOP:
                    break
            is_window = video_pipeline.state.get("source") == "window"
            ts = wgc.latest_ts()
            if ts and ts == prev_ts:
                time.sleep(LOOP_POLL_GAP)     # 還是同一格,不重複採樣
                continue
            if not ts:
                # WGC 沒有時間戳(擷取還沒起來,或走 mss 備援)—— 沒有「新格了沒」
                # 可問,只能自己節流。少了這行會變成不受限的忙迴圈,把一顆核心吃滿。
                time.sleep(LOOP_POLL_GAP)
            prev_ts = ts
            frame = minimap._grab_window()
            if frame is None:
                ingest(None, None, reason="no_frame")
                _set_overlay(reason="no_frame", boxes=[], frame=None, fps=fps, is_window=is_window)
                had_prev = False
                time.sleep(0.05)
                continue
            h, w = frame.shape[:2]

            if i % LOOP_DETR_EVERY == 0:
                if not rune_detr.available():
                    ingest(None, None, reason="no_model")
                    _set_overlay(reason="no_model", boxes=[], frame=(w, h), fps=fps, is_window=is_window)
                    had_prev = False
                    time.sleep(0.2)
                    continue
                boxes4 = rune_detr.detect_arrows(frame)
                cands = _candidates(viz_rune_detect, frame)
            i += 1

            if not boxes4 or len(boxes4) != 4:
                ingest(None, None, reason="no_boxes")
                _set_overlay(reason="no_boxes", boxes=_pack(cands, None, None, w, h),
                             frame=(w, h), fps=fps, is_window=is_window)
                had_prev = False
                continue

            angles = []
            for box in boxes4:
                x0, y0, x1, y1 = (int(round(v)) for v in box)
                crop = frame[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
                angles.append([rune_wheel.angle_of(crop)])
            status = ingest(boxes4, angles, gap=1 if had_prev else FRAME_GAP)
            had_prev = True

            fps_n += 1
            dt = time.monotonic() - fps_t0
            if dt >= 1.0:
                fps = round(fps_n / dt, 1)
                fps_t0, fps_n = time.monotonic(), 0
            _set_overlay(reason="", frame=(w, h), fps=fps,
                         boxes=_pack(cands, boxes4, status.get("arrows"), w, h),
                         n_frames=status.get("n_frames", 0), is_window=is_window)
    except Exception as e:
        _set_overlay(reason=f"loop_error: {e!r}", boxes=[], frame=None, fps=0.0)
    finally:
        wgc.request_full_rate(False)
        with _loop_lock:
            globals()["_loop_thread"] = None


def _candidates(viz, frame):
    """全部候選框 + 信心分數,只留 OVERLAY_MIN_SCORE 以上。拿不到就回空 list。"""
    if viz is None:
        return []
    try:
        boxes, scores, _by0 = viz.raw_detections(frame)
    except Exception:
        return []
    if boxes is None:
        return []
    return [(boxes[k], float(scores[k])) for k in range(len(scores))
            if scores[k] >= OVERLAY_MIN_SCORE]


def _pack(cands, boxes4, arrows, w, h):
    """候選框 + 選中的 4 支 → 前端要的正規化清單。

    選中的那 4 支【不從候選裡挑】而是各自帶進來:幾何選擇挑中的框與候選框
    是同一批數字沒錯,但比對浮點座標找對應只會製造對不上的邊界情況。直接標
    sel=索引,前端照畫即可。"""
    out = []
    for box, score in cands:
        nb = _norm_box(box, w, h)
        if nb:
            out.append({"box": nb, "score": round(score, 3), "sel": None,
                        "dir": None, "motion": None})
    for k, box in enumerate(boxes4 or []):
        nb = _norm_box(box, w, h)
        if not nb:
            continue
        a = arrows[k] if arrows and k < len(arrows) else None
        out.append({"box": nb, "score": None, "sel": k,
                    "dir": (a or {}).get("direction"),
                    "motion": (a or {}).get("motion")})
    return out


def _set_overlay(**patch):
    global _overlay
    with _loop_lock:
        _overlay = {**_overlay, **patch}


def get_overlay():
    """疊圖資料(正規化框 + 逐支判定)。【讀這支就會啟動背景迴圈】,不讀就會自己停。"""
    global _loop_thread, _loop_last_read
    with _loop_lock:
        _loop_last_read = time.monotonic()
        alive = _loop_thread is not None and _loop_thread.is_alive()
        if not alive:
            _loop_thread = threading.Thread(target=_loop_body, name="rune-overlay",
                                            daemon=True)
            _loop_thread.start()
        snap = dict(_overlay)
    snap["arrows"] = get_last_status().get("arrows")
    snap["running"] = True
    return snap


def loop_running():
    with _loop_lock:
        return _loop_thread is not None and _loop_thread.is_alive()


def stop_loop(wait=1.5):
    """立刻停掉背景迴圈(測試與關機用)。正常使用不必呼叫 —— 沒人讀就會自己停。"""
    global _loop_last_read
    with _loop_lock:
        _loop_last_read = 0.0
        th = _loop_thread
    if th is not None:
        th.join(timeout=wait)
