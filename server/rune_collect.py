# -*- coding: utf-8 -*-
"""站在符文上反覆按 B 採集資料。【不移動角色、不解謎、不按方向鍵】。

用途:強化視覺模型與旋轉箭頭判斷。使用者實測靜態判向成功率不到七成 ——
複雜背景、多怪物、深藍色系箭頭常判錯 —— 需要更多真實樣本。

【為什麼跑在服務裡而不是獨立腳本】序列埠(COM3)被執行中的服務獨佔,另開行程
按鍵會 PermissionError(DEV_LOG 記過這個坑)。所以採集必須借用服務已連上的鍵盤。

【為什麼不自動標方向】判向器本身就是要被改善的對象,拿它的輸出當標籤等於把錯誤
固化進資料集 —— 這個專案先前吃過一模一樣的虧:用 find_capsule 的框裁訓練圖,而
那支函式誤差 >20px 的佔 41%,整批訓練資料等於「背景配正確標籤」。所以這裡【只存
原始資料 + 所有可觀測的中間值】,標籤留到之後用可靠的方式補。

每一筆存:
  band.png   搜尋帶裁切(與既有 rune_dataset 同尺度,可直接混用)
  full.png   【完整幀】—— 既有資料集只存搜尋帶,導致 search_x 這類需要完整幀
             才能驗證的東西無從驗起
  meta.json  偵測框、逐幀角度、靜止/旋轉分類、模板判向輸出(僅供對照,不是標籤)
  seq/       只有判定為旋轉時才存:一小段連續幀(約一圈),供旋轉資料集用
"""
import json
import os
import time

import cv2

import paths

OUT_DIR = paths.data_dir("rune_collect")

RENDER_WAIT = 0.7        # 按 B 後等謎題渲染
BURST_SECS = 1.2         # 連拍秒數。旋轉週期實測約 0.9 秒,要涵蓋一整圈才抓得到晃動
ROT_DEG_PER_FRAME = 6.0  # 每幀角度變化超過這個值 → 判定在轉(實測旋轉約 13 度/幀)


def capture_once(idx):
    """按一次 B → 等渲染 → 抓一段連拍 → 存檔。回 meta dict。

    不按方向鍵、不導航 —— 使用者要求角色留在原地,這支只負責看。
    """
    import minimap
    import rune
    import rune_cv
    import rune_detr
    import rune_wheel
    import wgc

    if rune._keyboard is None:
        return {"ok": False, "err": "鍵盤未連線"}
    if rune._focus_fn:
        rune._focus_fn()
    rune._tap(rune.ACTIVATE_KEY)
    time.sleep(RENDER_WAIT)

    frame = minimap._grab_window()
    if frame is None:
        return {"ok": False, "err": "拿不到遊戲畫面"}
    h, w = frame.shape[:2]
    by0, by1 = rune_cv.search_band(h)

    boxes4 = rune_detr.detect_arrows(frame)
    meta = {"i": idx, "ts": time.time(), "frame_wh": [w, h], "band_y0": int(by0),
            "boxes": None, "angles": None, "motion": "unknown",
            # 模板判向的輸出【只供對照,不是標籤】—— 它正是要被改善的對象
            "template_dirs": None}

    seq, angles = [], []
    if boxes4:
        meta["boxes"] = [[float(v) for v in b] for b in boxes4]
        meta["template_dirs"] = rune_cv._read_dirs_detr(frame, boxes4)[0]
        wgc.request_full_rate(True)
        try:
            t_end = time.time() + BURST_SECS
            while time.time() < t_end:
                f = minimap._grab_window()
                if f is None:
                    continue
                seq.append(f)
                angles.append([rune_wheel.angle_of(
                    f[int(b[1]):int(b[3]), int(b[0]):int(b[2])]) for b in boxes4])
                time.sleep(0.005)
        finally:
            wgc.request_full_rate(False)   # 引用計數,漏還原會讓擷取一直全速跑
        meta["angles"] = angles

        # 靜止/旋轉分類刻意【不用模板判向】,只看角度序列的變化幅度
        for k in range(len(boxes4)):
            vals = [a[k] for a in angles if a[k] is not None]
            if len(vals) < 5:
                continue
            mx = max(abs((vals[i] - vals[i - 1] + 180) % 360 - 180)
                     for i in range(1, len(vals)))
            if mx > ROT_DEG_PER_FRAME:
                meta["motion"] = "rotating"
                break
        else:
            if angles:
                meta["motion"] = "static"

    d = os.path.join(OUT_DIR, f"{idx:04d}")
    os.makedirs(d, exist_ok=True)
    cv2.imwrite(os.path.join(d, "band.png"), frame[by0:by1])
    cv2.imwrite(os.path.join(d, "full.png"), frame)
    if meta["motion"] == "rotating" and seq:
        sd = os.path.join(d, "seq")
        os.makedirs(sd, exist_ok=True)
        for j, f in enumerate(seq):
            cv2.imwrite(os.path.join(sd, f"{j:03d}.png"), f[by0:by1])
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False)

    meta["ok"] = bool(boxes4)
    meta["n_frames"] = len(seq)
    meta.pop("angles", None)          # 回給前端的不帶逐幀角度,太長
    return meta
