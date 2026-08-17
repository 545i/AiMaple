# -*- coding: utf-8 -*-
"""站在符文上反覆按 B 抓資料。【不移動角色、不解謎、不按方向鍵】。

用途:強化視覺模型與旋轉箭頭判斷。使用者實測靜態判向成功率不到七成 ——
複雜背景、多怪物、深藍色系箭頭常判錯 —— 所以需要更多真實樣本。

【為什麼不自動標方向】判向器本身就是要被改善的對象,拿它的輸出當標籤等於把
錯誤固化進資料集(這個專案先前吃過一模一樣的虧:用 find_capsule 的框裁訓練圖,
而那支函式誤差 >20px 的佔 41%,整批訓練資料等於背景配正確標籤)。
所以這裡【只存原始資料 + 所有可觀測的中間值】,標籤留到之後用可靠的方式補。

存下來的每一筆包含:
  - band.png     搜尋帶裁切(與 rune_dataset 既有樣本同一個尺度,可直接混用)
  - full.png     完整幀(【新】—— 既有資料集只存了搜尋帶,導致 search_x 這類
                 需要完整幀才能驗證的東西無從驗起)
  - meta.json    偵測框、每支的角度、靜止/旋轉分類、模板判向的輸出(僅供對照,
                 不是標籤)、時間戳
  - seq/*.png    只有【判定為旋轉】時才存:一小段連續幀(約一圈),供旋轉資料集用

用法(在專案根目錄):
    venv/Scripts/python.exe -X utf8 tools/collect_rune_data.py --n 300 --gap 15
"""
import argparse
import json
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

OUT_DIR = os.path.join(ROOT, "rune_collect")


def _grab(minimap):
    return minimap._grab_window()


def capture_once(idx, args, mods):
    """按一次 B → 等渲染 → 抓資料。回 dict(這一筆的 meta)或 None。"""
    rune, minimap, rune_cv, rune_detr, rune_wheel, wgc = mods
    rune._tap(rune.ACTIVATE_KEY)
    time.sleep(args.render_wait)

    frame = _grab(minimap)
    if frame is None:
        return None
    h, w = frame.shape[:2]
    by0, by1 = rune_cv.search_band(h)
    band = frame[by0:by1]

    boxes4 = rune_detr.detect_arrows(frame)
    meta = {
        "i": idx, "ts": time.time(), "frame_wh": [w, h], "band_y0": int(by0),
        "boxes": None, "angles": None, "motion": None,
        "template_dirs": None,      # 僅供對照,【不是標籤】—— 它正是要被改善的對象
    }
    if boxes4:
        meta["boxes"] = [[float(v) for v in b] for b in boxes4]
        dirs, _err = rune_cv._read_dirs_detr(frame, boxes4)
        meta["template_dirs"] = dirs

    # 連拍一小段:靜止/旋轉都要,才判得出是哪一種(單幀分不出來)
    seq, angles = [], []
    if boxes4:
        wgc.request_full_rate(True)
        try:
            t_end = time.time() + args.burst
            while time.time() < t_end:
                f = _grab(minimap)
                if f is None:
                    continue
                seq.append(f)
                angles.append([rune_wheel.angle_of(f[int(b[1]):int(b[3]),
                                                    int(b[0]):int(b[2])])
                               for b in boxes4])
                time.sleep(0.005)
        finally:
            wgc.request_full_rate(False)      # 引用計數,漏還原會讓擷取一直全速
        meta["angles"] = angles

    d = os.path.join(OUT_DIR, f"{idx:04d}")
    os.makedirs(d, exist_ok=True)
    cv2.imwrite(os.path.join(d, "band.png"), band)
    cv2.imwrite(os.path.join(d, "full.png"), frame)

    # 靜止/旋轉分類:用角度序列的變化幅度,不用模板判向
    rotating = False
    if angles:
        for k in range(len(boxes4)):
            vals = [a[k] for a in angles if a[k] is not None]
            if len(vals) < 5:
                continue
            spread = max((abs((vals[i] - vals[i - 1] + 180) % 360 - 180))
                         for i in range(1, len(vals)))
            if spread > 6:                     # 每幀轉超過 6 度 = 在轉
                rotating = True
    meta["motion"] = "rotating" if rotating else ("static" if angles else "unknown")

    if rotating and seq:
        sd = os.path.join(d, "seq")
        os.makedirs(sd, exist_ok=True)
        for j, f in enumerate(seq):
            cv2.imwrite(os.path.join(sd, f"{j:03d}.png"), f[by0:by1])

    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--gap", type=float, default=15.0, help="每次擷取之間等待秒數")
    ap.add_argument("--render-wait", type=float, default=0.7, help="按 B 後等渲染")
    ap.add_argument("--burst", type=float, default=1.2, help="連拍秒數(約一圈)")
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()

    import minimap, rune, rune_cv, rune_detr, rune_wheel, wgc
    import arduino, config
    # 直接接鍵盤:這支腳本不經過 main.py 的 lifespan,rune._keyboard 是 None
    if rune._keyboard is None:
        kb = arduino.Arduino(config.ARDUINO_PORT, config.ARDUINO_BAUD)
        rune.set_hooks(keyboard=kb)
    mods = (rune, minimap, rune_cv, rune_detr, rune_wheel, wgc)

    os.makedirs(OUT_DIR, exist_ok=True)
    stat = {"ok": 0, "no_frame": 0, "no_boxes": 0, "static": 0, "rotating": 0}
    t0 = time.time()
    for i in range(args.start, args.start + args.n):
        try:
            m = capture_once(i, args, mods)
        except Exception as e:
            print(f"[{i}] 例外: {e!r}", flush=True)
            m = None
        if m is None:
            stat["no_frame"] += 1
        elif not m["boxes"]:
            stat["no_boxes"] += 1
        else:
            stat["ok"] += 1
            stat[m["motion"]] = stat.get(m["motion"], 0) + 1
        if (i + 1) % 10 == 0 or i == args.start:
            el = time.time() - t0
            done = i - args.start + 1
            print(f"[{done}/{args.n}] {stat}  已用 {el/60:.1f} 分  "
                  f"剩約 {(el/done*(args.n-done))/60:.0f} 分", flush=True)
        time.sleep(args.gap)
    print(f"完成 {stat}  輸出於 {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
