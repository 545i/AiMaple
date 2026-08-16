# -*- coding: utf-8 -*-
"""從影片量出旋轉符文的時序規律。

【⚠ 這支工具的原始前提已被實測推翻,保留是為了記錄那個錯誤】
寫它的時候以為機制是「箭頭轉一圈、在答案方向【停頓】約 0.5 秒」,所以用幀間差分
找靜止區間。實測結果:**根本沒有靜止區間** —— 背景特效一直在動,而且箭頭本身也
從來沒有真的停下來過(逐幀量角度,每一幀都在變,約 -13 度/幀)。

真正的機制是【晃動】,不是停頓 —— 遊戲提示原文就寫著「找尋箭頭晃動的方向」。
箭頭經過答案方向時會【倒退 2 幀(約 67ms)】再恢復原速,幅度小、時間短,幀間差分
完全抓不到。

【可用的做法在 server/rune_wheel.py】箭頭的漸層(綠尾→黃→紅頭)本身編碼了指向,
`atan2(紅重心 − 綠重心)` 對任意角度都準;再找角速度反轉點就是晃動。已用真實影片
驗證,答對「下、左、下、上」。

這支工具現在只剩 --dump-frames 還有用(抽幀決定 ROI)。時序分析請改用
tools/eval_rune_wheel.py。

--------------------------------------------------------------------------
以下是原始說明(前提已錯,僅供對照)
--------------------------------------------------------------------------
從影片量出旋轉符文的【時序規律】:旋轉週期多長、停頓多久、停幾次。

【為什麼可以用影片,即使尺度對不上】
訓練/測試素材不能用影片(尺度不同、YouTube 的 4:2:0 色度壓縮正好打在判向器
依賴的色度分割上、而且沒有 purple_gone 那種自動真值)。但【時序】是尺度無關的:
旋轉時畫面在動、停頓時畫面靜止,用幀間差分就切得出來,不需要認出箭頭是哪一支、
指哪個方向。所以影片拿來偵察時序完全可以。

【為什麼要知道時序】
判向那段(色度分割 + 四方向模板)只認上下左右,實測箭頭轉到 29~41 度就會讀錯
(合成測試集:停在正方向 78.1%、旋轉中掉到 41.0%)。所以唯一可靠的讀法是
【只在停頓窗內讀】。要做到就得知道:停頓多久(決定要抓幾幀)、週期多長
(決定最多要等多久)、是不是只停在答案上(決定停頓能不能直接當答案)。

用法:
    # 1) 先抓幾張幀出來看,決定 ROI(膠囊在畫面裡的位置)
    venv/Scripts/python.exe -X utf8 tools/analyze_rune_timing.py VIDEO --dump-frames 8

    # 2) 指定 ROI 後量時序
    venv/Scripts/python.exe -X utf8 tools/analyze_rune_timing.py VIDEO --roi 640,300,340,90
"""
import argparse
import os
import sys

import cv2
import numpy as np


def dump_frames(cap, n, out_dir):
    """均勻抽 n 張幀存檔,供人工決定 ROI。"""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    os.makedirs(out_dir, exist_ok=True)
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n))
        ok, f = cap.read()
        if ok:
            p = os.path.join(out_dir, f"frame_{i:02d}.png")
            cv2.imwrite(p, f)
            print(f"  {p}  ({f.shape[1]}x{f.shape[0]})")


def motion_signal(cap, roi):
    """回 (時間軸秒, 每幀相對前一幀的變動量)。

    變動量用 ROI 內的平均絕對差。刻意不做二值化或形態學 —— 這裡只要相對大小,
    不需要絕對意義,越少假設越不容易被壓縮假影帶偏。
    """
    x, y, w, h = roi
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prev = None
    ts, sig = [], []
    idx = 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        crop = f[y:y + h, x:x + w]
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            ts.append(idx / fps)
            sig.append(float(np.abs(g - prev).mean()))
        prev = g
        idx += 1
    return np.array(ts), np.array(sig), fps


def find_dwells(ts, sig, fps, quiet_ratio=0.35, min_dwell_s=0.15):
    """把「靜止區間」切出來。

    門檻取【中位數的 quiet_ratio 倍】而不是固定值 —— 不同影片的亮度/壓縮程度
    差很多,固定門檻不可移植。min_dwell_s 濾掉單幀雜訊造成的假靜止。
    """
    if len(sig) == 0:
        return [], 0.0
    thr = float(np.median(sig)) * quiet_ratio
    quiet = sig < thr
    dwells = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(quiet) and quiet[j + 1]:
            j += 1
        dur = (j - i + 1) / fps
        if dur >= min_dwell_s:
            dwells.append((ts[i], ts[j], dur))
        i = j + 1
    return dwells, thr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--roi", default=None, help="x,y,w,h(膠囊那一帶)")
    ap.add_argument("--dump-frames", type=int, default=0)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--quiet-ratio", type=float, default=0.35)
    ap.add_argument("--min-dwell", type=float, default=0.15)
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print(f"找不到影片:{args.video}")
        sys.exit(1)
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print("影片打不開(編碼不支援?)")
        sys.exit(1)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"影片 {w}x{h}  {fps:.2f}fps  {n} 幀  {n / max(fps, 1):.1f} 秒\n")

    out_dir = args.out_dir or os.path.join(os.path.dirname(args.video), "_timing")
    if args.dump_frames:
        print(f"抽 {args.dump_frames} 張幀 → {out_dir}")
        dump_frames(cap, args.dump_frames, out_dir)
        print("\n看完這些圖決定膠囊的位置,再用 --roi x,y,w,h 重跑。")
        return

    if not args.roi:
        print("需要 --roi。先跑 --dump-frames 8 看圖決定範圍。")
        sys.exit(1)
    roi = tuple(int(v) for v in args.roi.split(","))
    ts, sig, fps = motion_signal(cap, roi)
    dwells, thr = find_dwells(ts, sig, fps, args.quiet_ratio, args.min_dwell)

    print(f"ROI {roi}  變動量:中位 {np.median(sig):.2f}  靜止門檻 {thr:.2f}\n")
    if not dwells:
        print("沒有偵測到靜止區間 —— ROI 可能不對,或這段沒有旋轉符文。")
        return
    print(f"偵測到 {len(dwells)} 段靜止:")
    for a, b, d in dwells:
        print(f"  {a:6.2f}s ~ {b:6.2f}s   持續 {d * 1000:5.0f} ms")
    ds = [d for _a, _b, d in dwells]
    print(f"\n停頓時長:中位 {np.median(ds) * 1000:.0f} ms  "
          f"最短 {min(ds) * 1000:.0f}  最長 {max(ds) * 1000:.0f}")
    if len(dwells) > 1:
        gaps = [dwells[i + 1][0] - dwells[i][0] for i in range(len(dwells) - 1)]
        print(f"停頓間隔(週期):中位 {np.median(gaps):.2f}s  "
              f"最短 {min(gaps):.2f}  最長 {max(gaps):.2f}")
    print("\n【怎麼用這些數字】停頓時長決定要在窗內抓幾幀(現在 GPU 27.8ms/幀);"
          "\n週期決定最多要等多久才等得到下一次停頓。")


if __name__ == "__main__":
    main()
