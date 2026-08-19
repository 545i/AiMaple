# -*- coding: utf-8 -*-
"""菲歐娜解謎【觀察模式】:你正常玩,它在旁邊記「我會選哪個」,再拿計分格對答案。

    venv\\Scripts\\python.exe tools\\fiona_watch.py
    venv\\Scripts\\python.exe tools\\fiona_watch.py --video 某段錄影.mp4   # 離線重跑
    venv\\Scripts\\python.exe tools\\fiona_watch.py --summary              # 只看統計

【不會按任何鍵、不會點滑鼠】。目前單輪正確率只有 11/12(91.7%),12 輪的 95%
信賴區間是 [64.6%, 98.5%] —— 寬到無法判斷能不能用,而一場 4 輪錯一輪就受懲罰,
不該拿真實遊戲下注。先累積資料,數字夠了再談要不要接上點擊。

【可以獨立行程跑】不像 server/rune_collect.py 必須寄生在服務裡(那是因為序列埠
被服務獨佔),這支只讀畫面、不碰序列埠,所以直接執行就行。

輸出到 fiona_collect/NNNN/,每輪一筆 meta.json(+ bands.npz)。真值一律來自
遊戲自己畫的計分格,不從任何模型/演算法的輸出。
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "server"))

from fiona_collect import FionaCollector  # noqa: E402

OUT_DIR = os.path.join(HERE, "fiona_collect")


def _frames_live():
    """實機幀來源。走 minimap._grab_window() —— 與符文/巡邏共用同一份影格
    (frames.get() 是 WGC → mss 備援的單一來源,不要另外開一套抓圖)。"""
    import minimap
    miss = 0
    while True:
        f = minimap._grab_window()
        if f is None:
            miss += 1
            if miss % 40 == 1:
                print("  (抓不到遊戲畫面,等待中…)")
            time.sleep(0.25)
            continue
        miss = 0
        yield f, time.time()


def _frames_video(path):
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"開不了影片:{path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield f, i / fps
        i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="離線重跑一段錄影,而不是抓實機畫面")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--summary", action="store_true", help="只印統計後離開")
    ap.add_argument("--no-bands", action="store_true",
                    help="不存原始帶序列(省磁碟,但之後無法重跑改進後的追蹤器)")
    a = ap.parse_args()

    if a.summary:
        if not os.path.isdir(a.out):
            sys.exit(f"還沒有採集資料:{a.out}")
        print(FionaCollector.summarize(a.out))
        return

    col = FionaCollector(out_dir=a.out, save_bands=not a.no_bands)
    src = _frames_video(a.video) if a.video else _frames_live()
    print(f"觀察模式啟動(只記錄,不點擊)。輸出 → {a.out}")
    if not a.video:
        print("正常玩就好,Ctrl+C 結束。\n")

    n = ok = 0
    try:
        for frame, ts in src:
            r = col.ingest(frame, ts=ts)
            if not r or r["event"] != "round":
                continue
            if r["truth"] is None:
                print(f"  第{r['round_idx']+1}輪  預測={r['pred']}  真值=(答錯/超時,拿不到)")
                continue
            n += 1
            ok += int(bool(r["correct"]))
            mark = "O" if r["correct"] else "X"
            print(f"  第{r['round_idx']+1}輪  預測={r['pred']}  真值={r['truth']}  {mark}"
                  f"   累計 {ok}/{n} = {ok / n * 100:.1f}%")
    except KeyboardInterrupt:
        print("\n中止。")
    print("\n統計:", FionaCollector.summarize(a.out))


if __name__ == "__main__":
    main()
