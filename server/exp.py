# -*- coding: utf-8 -*-
"""EXP 進度偵測:讀遊戲畫面最下方經驗條【上面的文字】,回經驗值與百分比。

--------------------------------------------------------------------------
為什麼讀文字,而不是量填充長度
--------------------------------------------------------------------------
經驗條已填滿與未填滿的部分顏色不同,量兩段長度也能推出百分比 —— 但那是劣化的做法:
  * 底色會變:使用者實測經驗值低於 60% 時填充色換成另一種顏色。綁死顏色就會失效,
    而且是那種「大部分時候看起來正常、低經驗時才壞」的隱性故障。
  * 精度差:1368px 寬的條,一個像素就是 0.073%;而條上的文字直接寫著小數點後三位。
文字是遊戲自己算好的權威值,量像素只是它的近似。

--------------------------------------------------------------------------
為什麼是模板比對,不是 OCR
--------------------------------------------------------------------------
遊戲 UI 是點陣字,字型/字級/顏色全固定,實測字元尺寸極度規整:
    數字 5x7    逗號 2x2    句點 1x1    '1' '[' ']' 2x7    '%' = 3x3 + 7x7 + 3x3
這種條件下模板比對是【精確匹配】而不是近似辨識。反過來,Tesseract 這類 OCR 對
7px 高的點陣字辨識率很差,還要多背一個幾十 MB 的相依 —— 換來更差的結果。

--------------------------------------------------------------------------
白字判定不碰背景色
--------------------------------------------------------------------------
用【無彩色】判定:B/G/R 三通道都夠亮【且彼此接近】。白字 (255,255,255) 通過;
任何彩色背景(黃/綠/紅…)都因為通道差距過大被排除。所以經驗條換色不影響辨識 ——
這正是「只抓文字顏色、不抓背景色」的要求。單用灰階門檻做不到這件事:亮黃色
(0,245,235) 的灰階高達 224,跟白字只差一點,換個亮底色就會整片誤收。
"""
import re
import threading
import time

import cv2
import numpy as np

# 白字判定。MIN_CH:三通道都要夠亮;MAX_SPREAD:通道差距上限(白=無彩色,差距接近 0)。
# 實測黃底 BGR(0,245,235) 的 spread 高達 245,離門檻極遠,不會誤收。
WHITE_MIN_CH = 200
WHITE_MAX_SPREAD = 40

# 搜尋範圍:經驗條固定在遊戲視窗最底部。只掃最下面這麼多列,避免把畫面中央的
# 白色傷害數字、玩家暱稱等一併收進來(那些字級不同,尺寸過濾也擋得掉,但先縮範圍更省)。
BOTTOM_BAND = 32

GLYPH_H = 7            # 數字/括號的字高
MAX_GLYPH_W = 8        # 單一字元寬度上限;超過的必是別的東西(如填充邊界線)
MIN_CHARS = 8          # 少於這麼多字元就不認為找到了經驗條文字

# 點陣模板:key=(寬,高),value={bit字串: 字元}。bit 字串為 row-major 的 '0'/'1'。
#
# 【怎麼來的】掃 822 幀實機畫面,把所有白字連通元件依尺寸+位元圖樣去重,得到 16 種。
# 其中 5x7 恰好 9 種 —— 正是 0,2,3,4,5,6,7,8,9(數字 1 是窄體 2x7),不多不少,
# 這本身就是「字型固定、無反鋸齒」的佐證。字元標籤由放大圖人工判讀,再用出現次數
# 交叉驗證:',' 3288 次 = 822 幀 x 4 個逗號、'.' 822 次 = 每幀 1 個、'%' 的上下圈
# 1644 次 = 每幀 2 個,全部吻合。
GLYPHS = {
    (1, 1): {
        "1": ".",
    },
    (2, 2): {
        "0110": ",",
    },
    (2, 7): {
        "01110101010101": "1",
        "11101010101011": "[",
        "11010101010111": "]",
    },
    (5, 7): {
        "01110100011000110001100011000101110": "0",
        "01110100010000100010001000100011111": "2",
        "01110100010000100110000011000101110": "3",
        "00010001100101010010111110001000010": "4",
        "11111100001000001110000011000101110": "5",
        "01110100011000011110100011000101110": "6",
        "11111000010000100010000100010000100": "7",
        "01110100011000101110100011000101110": "8",
        "01110100011000101111000011000101110": "9",
    },
}


def _white_mask(bgr):
    """白字遮罩。見模組說明:無彩色判定,不依賴背景色。"""
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    mn = np.minimum(np.minimum(b, g), r)
    mx = np.maximum(np.maximum(b, g), r)
    return ((mn >= WHITE_MIN_CH) & (mx - mn <= WHITE_MAX_SPREAD)).astype(np.uint8)


def _components(mask):
    """白遮罩內的字元候選,依 x 排序。回 [{x,y,w,h,bits}]。"""
    n, lab, st, _ce = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x = int(st[i, cv2.CC_STAT_LEFT])
        y = int(st[i, cv2.CC_STAT_TOP])
        w = int(st[i, cv2.CC_STAT_WIDTH])
        h = int(st[i, cv2.CC_STAT_HEIGHT])
        if w > MAX_GLYPH_W or h > GLYPH_H + 1:
            continue                      # 填充邊界線(h=9)之類的非文字物件
        patch = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        out.append({"x": x, "y": y, "w": w, "h": h,
                    "bits": "".join(str(v) for v in patch.ravel())})
    out.sort(key=lambda c: c["x"])
    return out


def _classify(c):
    """單一元件 → 字元。認不出來回 '?'。

    '%' 由三塊組成(上圈 3x3、斜線 7x7、下圈 3x3),三塊都先各自回 '%',
    再由 _join 把連續的合併成一個。"""
    key = (c["w"], c["h"])
    if key in ((3, 3), (7, 7)):
        return "%"
    tpl = GLYPHS.get(key)
    if not tpl:
        return "?"
    ch = tpl.get(c["bits"])
    if ch is not None:
        return ch
    # 精確匹配失敗才退回最近鄰(壓縮雜訊/半透明疊層可能翻動 1~2 個位元)。
    # 距離超過 3 就別硬猜 —— 猜錯一位數字比回報「讀不到」糟得多。
    best, bd = "?", 99
    for bits, cand in tpl.items():
        dist = sum(1 for a, b in zip(bits, c["bits"]) if a != b)
        if dist < bd:
            best, bd = cand, dist
    return best if bd <= 3 else "?"


def _join(chars):
    """把連續重複的 '%' 併成一個(它本來就是三塊拼出來的)。"""
    out = []
    for ch in chars:
        if ch == "%" and out and out[-1] == "%":
            continue
        out.append(ch)
    return "".join(out)


def read_text(frame_bgr):
    """回 (文字, 元件數)。找不到回 ("", 0)。"""
    if frame_bgr is None:
        return "", 0
    h, w = frame_bgr.shape[:2]
    band = frame_bgr[max(0, h - BOTTOM_BAND):h, :]
    comps = _components(_white_mask(band))
    if len(comps) < MIN_CHARS:
        return "", len(comps)
    # 文字在同一條基線上。取字高 GLYPH_H 那些元件的 y 眾數當基線,只保留貼齊它的 ——
    # 經驗條上方還有 HP/MP 數字等其他白字,不對齊基線的一律排除。
    tall = [c["y"] for c in comps if c["h"] == GLYPH_H]
    if not tall:
        return "", len(comps)
    base = int(np.bincount(np.array(tall)).argmax())
    line = [c for c in comps if base - 1 <= c["y"] <= base + GLYPH_H]
    if len(line) < MIN_CHARS:
        return "", len(line)
    return _join(_classify(c) for c in line), len(line)


_NUM = re.compile(r"^([\d,]+)\s*\[([\d.]+)%\]$")


def read(frame_bgr):
    """讀經驗值與百分比。回 dict:
        {"ok":bool, "exp":int|None, "pct":float|None, "text":str, "err":str}
    text 一律回傳(即使解析失敗),那是診斷「哪個字元認錯」的唯一線索。"""
    text, n = read_text(frame_bgr)
    if not text:
        return {"ok": False, "exp": None, "pct": None, "text": "",
                "err": f"找不到經驗條文字(白字元件 {n} 個)"}
    if "?" in text:
        return {"ok": False, "exp": None, "pct": None, "text": text,
                "err": "有字元認不出來"}
    m = _NUM.match(text)
    if not m:
        return {"ok": False, "exp": None, "pct": None, "text": text,
                "err": "格式不符(預期「數字 [百分比%]」)"}
    try:
        exp = int(m.group(1).replace(",", ""))
        pct = float(m.group(2))
    except ValueError:
        return {"ok": False, "exp": None, "pct": None, "text": text,
                "err": "數值轉換失敗"}
    return {"ok": True, "exp": exp, "pct": pct, "text": text, "err": ""}


def read_now():
    """抓一幀直接讀。與巡邏/符文共用同一個影格來源。"""
    import frames
    f, _is_window = frames.get()
    return read(f)


# ---------- 累計進度 ----------
# 只在讀成功時更新,讀失敗(切地圖、UI 遮住、視窗最小化)不動基準,避免把
# 「暫時讀不到」錯當成經驗歸零。
_lock = threading.Lock()
_track = {"last": None, "acc": 0, "t0": None, "levels": 0}


def reset():
    """把累計基準歸零到目前經驗值。"""
    with _lock:
        _track.update({"last": None, "acc": 0, "t0": None, "levels": 0})
    return status()


def status():
    """目前經驗值 + 本次累計。read() 的欄位之外多回:
        gained  本次累計獲得的經驗(跨等級的那一段不計,見下)
        rate    每小時經驗(依實際經過時間外推)
        levels  期間升了幾級
        secs    已追蹤秒數
    """
    r = read_now()
    if not r["ok"]:
        with _lock:
            r.update({"gained": _track["acc"], "rate": None,
                      "levels": _track["levels"], "secs": None})
        return r
    now = time.monotonic()
    with _lock:
        if _track["t0"] is None:
            _track["t0"] = now
        last = _track["last"]
        if last is not None:
            if r["exp"] >= last:
                _track["acc"] += r["exp"] - last
            else:
                # 經驗值變小 = 升級了。升一級需要的總量我們不知道(那要查表),
                # 所以【跨級的那一段不計入累計】,只記升級次數 —— 寧可少算,
                # 也不要用猜的數字污染統計。
                _track["levels"] += 1
        _track["last"] = r["exp"]
        secs = now - _track["t0"]
        acc = _track["acc"]
        r.update({"gained": acc,
                  "rate": int(acc / secs * 3600) if secs > 1 else None,
                  "levels": _track["levels"], "secs": round(secs, 1)})
    return r
