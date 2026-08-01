# -*- coding: utf-8 -*-
"""產生符文膠囊的定位真值 rune_dataset/box_truth.json。

--------------------------------------------------------------------------
為什麼需要真值
--------------------------------------------------------------------------
改進膠囊定位時,唯一能用的指標本來是「最終四個方向對不對」—— 那太間接了:
定位偏 3px 和偏 300px 在指標上都只是「失敗」,無從分辨一個改動是往對的方向走
還是往錯的方向走。實測就吃過這個虧:閉運算、放寬 HSV、放大斷裂容忍、降低長度
門檻…每種都只在 21%~23% 之間游移,分不出誰有價值。

--------------------------------------------------------------------------
怎麼產生(不需要人工標註,也不需要 VLM)
--------------------------------------------------------------------------
dirs 真值是現成的(purple_gone 樣本由遊戲本身驗證過),所以【反過來】窮舉 box 位置,
找出哪些位置能判對全部 4 支 —— 判向在 box 正確時 716 支只錯 1 支,所以「判得對」
幾乎等價於「位置對」。

關鍵在取哪一點當答案:真實膠囊位置周圍會形成【連續】的可行區(箭頭寬 28、格寬 82,
box 偏 ±20px 內箭頭仍落在同一格,判向不變);而背景雜訊碰巧湊出相同方向組合的位置
是【孤立點】。第一版取全體命中點的中位數,被孤立點拉偏 —— 症狀是「可行區 x 寬度
中位 228px」這種不合理的數字,以及拿真值回頭定位也只有 58% 判對。改成取【最大連通
區的重心】後升到 89%。

【已知限制】89% 不是 100%,少數樣本的真值仍不可靠(blob_n 很小的那些尤其可疑,
使用時可拿 blob_n 當信心權重過濾)。

【已知現象,尚無解釋】真值顯示膠囊位置在 x 與 y 都會大幅變動(x 全距 406、y 全距
252),即使影格尺寸幾乎全同(1368x347 佔 155/157)。中心 x 中位正好落在畫面正中
(偏差 -2px),但散布達 p10 -62 / p90 +88,所以「固定位置」定位只有 20%。
一個尚未驗證的猜想:抓圖時膠囊還在出現動畫中(ACTIVATE_WAIT=0.55s),位置尚未定位。

跑一次約 20 分鐘(180 張)。
"""
import json
import os
import sys
import time

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "server"))
import rune_cv as R

D = os.path.join(_ROOT, "rune_dataset")
OUT = os.path.join(D, "box_truth.json")

# 搜尋範圍刻意放得比已知分布寬,免得真值落在框外。
# 小圖(使用者手動裁的截圖)整張都搜 —— 硬套下界會讓 444 寬的圖搜尋範圍變成空的。
X0, X1, Y0, Y1 = 350, 760, 40, 300
STEP = 4


def dirs_from_box(img, box):
    """用【指定的 box】判向,跳過 find_capsule。等同 rune_cv.read_dirs 的核心。"""
    x0, y0, x1, y1 = box
    if x0 < 0 or y0 < 0 or x1 > img.shape[1] or y1 > img.shape[0]:
        return []
    cap = img[y0:y1, x0:x1]
    if cap.size == 0:
        return []
    dist = R._chroma_map(cap)
    w = cap.shape[1] / 4
    out = []
    for k in range(4):
        m = R._seg(dist, int(k * w), int((k + 1) * w))
        out.append(None if m is None else (R._direction_tpl(m) or R._direction(m)))
    return out


def solve(img, truth):
    """回 (所有命中點, (重心x, 重心y, 該連通區點數))。見模組說明。"""
    xlo = 0 if img.shape[1] < 900 else X0
    ylo = 0 if img.shape[0] < 400 else Y0
    xhi = min(X1, img.shape[1] - R.CAP_W) if img.shape[1] >= 900 else img.shape[1] - R.CAP_W
    yhi = min(Y1, img.shape[0] - R.CAP_H) if img.shape[0] >= 400 else img.shape[0] - R.CAP_H
    hits = []
    for y in range(max(0, ylo), max(1, yhi + 1), STEP):
        for x in range(max(0, xlo), max(1, xhi + 1), STEP):
            if dirs_from_box(img, (x, y, x + R.CAP_W, y + R.CAP_H)) == truth:
                hits.append((x, y))
    if not hits:
        return [], None
    xs = [h[0] for h in hits]
    ys = [h[1] for h in hits]
    m = np.zeros(((max(ys) - min(ys)) // STEP + 1,
                  (max(xs) - min(xs)) // STEP + 1), np.uint8)
    for x, y in hits:
        m[(y - min(ys)) // STEP, (x - min(xs)) // STEP] = 255
    n, _lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return hits, None
    i = max(range(1, n), key=lambda j: int(st[j, cv2.CC_STAT_AREA]))
    return hits, (int(round(min(xs) + cen[i][0] * STEP)),
                  int(round(min(ys) + cen[i][1] * STEP)),
                  int(st[i, cv2.CC_STAT_AREA]))


def main():
    rows = [json.loads(l) for l in open(os.path.join(D, "index.jsonl"), encoding="utf-8")
            if l.strip()]
    pos = [r for r in rows if (r.get("dirs") or [])]
    out = []
    t0 = time.time()
    for i, r in enumerate(pos):
        img = R.imread(os.path.join(D, r["file"]))
        if img is None:
            continue
        cur = R.find_capsule(img)              # 描邊法目前的答案,供交叉比對
        hits, blob = solve(img, r["dirs"])
        rec = {"file": r["file"], "n_hits": len(hits),
               "cv_box": list(cur) if cur else None,
               "img_w": img.shape[1], "img_h": img.shape[0]}
        if hits:
            xs = [h[0] for h in hits]
            ys = [h[1] for h in hits]
            rec.update({"x_min": min(xs), "x_max": max(xs),
                        "y_min": min(ys), "y_max": max(ys)})
        if blob:
            rec.update({"x": blob[0], "y": blob[1], "blob_n": blob[2]})
        out.append(rec)
        if (i + 1) % 20 == 0 or i == len(pos) - 1:
            el = time.time() - t0
            got = sum(1 for o in out if o.get("x") is not None)
            print(f"  {i+1}/{len(pos)}  找到 {got}  已用 {el/60:.1f} 分  "
                  f"剩 {(el/(i+1)*(len(pos)-i-1))/60:.1f} 分", flush=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    got = sum(1 for o in out if o.get("x") is not None)
    print(f"\n完成:{got}/{len(out)} 張找到真值 → {OUT}")


if __name__ == "__main__":
    main()
