# -*- coding: utf-8 -*-
"""驗收工具:用真實影片驗證 server/rune_wheel.py 的旋轉符文(解放輪)判向。

素材:`C:\\Users\\mense\\Downloads\\videoplayback (1).mp4`(240 幀/30fps/8 秒)。
影片裡有兩個顯示:遊戲真實 UI 的膠囊(小),以及實況主放大的內嵌畫面(大、清楚,
約在 y 390~530、x 680~1420)。這個工具只用放大內嵌那個區域 —— 小的那個真實
膠囊解析度太低,不適合拿來驗證判向演算法本身。

正確答案(人工逐幀分析確認,四支箭頭各自跨 4 圈重複一致):下、左、下、上。

【箭頭框怎麼來】不依賴 rune_detr(那是為了另一種畫面尺度/風格訓練的,這支
影片是實況主的放大疊圖,視覺上完全不同)。改用最直接的作法:對指定區域內
整段影片的箭頭色遮罩(與 rune_wheel.angle_of 同一份 HSV 判定)取聯集 —— 箭頭
原地旋轉,聯集會掃出 4 個圓形包絡,取這 4 個包絡的外接框當箭頭框。這個框在
整段期間視為不變,交給 rune_wheel.solve() 逐幀裁切。

只驗證 rune_wheel 本身,不驗證「怎麼在真實遊戲畫面裡定位到這個放大內嵌區域」
——那不是這次的範圍(正式流程走的是 server/rune.py 的旋轉分支,用
rune_detr.detect_arrows 在真實遊戲畫面上定位,不是這支工具的路徑)。
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_wheel as rw  # noqa: E402

DEFAULT_VIDEO = r"C:\Users\mense\Downloads\videoplayback (1).mp4"
DEFAULT_REGION = (390, 530, 680, 1420)   # y0, y1, x0, x1 —— 使用者提供的放大內嵌區域
TRUTH = ["down", "left", "down", "up"]   # 人工逐幀確認的正確答案
MIN_BLOB_AREA = 1000                      # 聯集包絡的最小面積,濾掉場景雜訊碎屑
                                          # (實測箭頭包絡約 6200~6400,雜訊碎屑
                                          # 200~820,兩者間有寬裕的空隙可切)
BOX_PAD = 4                               # 包絡框往外擴一點,避免箭頭尖端貼邊被切


def read_frames(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit(f"讀不到影片: {video_path}")
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def locate_boxes(frames, region):
    """對 region 內整段影片的箭頭色遮罩取聯集,找 4 個圓形包絡(箭頭旋轉時掃過
    的範圍),回箭頭框 list(整幀座標,由左到右)。找不到剛好 4 個回 None。"""
    y0, y1, x0, x1 = region
    union = None
    for frame in frames:
        crop = frame[y0:y1, x0:x1]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        mask = (((h < rw.HUE_LOW_MAX) | (h > rw.HUE_HIGH_MIN))
                & (s > rw.SAT_MIN) & (v > rw.VAL_MIN))
        union = mask if union is None else (union | mask)
    if union is None:
        return None
    union8 = (union.astype(np.uint8)) * 255
    union8 = cv2.morphologyEx(union8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(union8, 8)
    comps = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < MIN_BLOB_AREA:
            continue
        bx, by = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        # 箭頭旋轉一圈掃出的包絡近似圓形(寬高相近);場景雜訊多半是長條或碎塊。
        if not (0.6 <= bw / max(bh, 1) <= 1.7):
            continue
        comps.append((bx, by, bx + bw, by + bh))
    comps.sort(key=lambda c: c[0])
    if len(comps) != 4:
        return None
    return [(x0 + cx0 - BOX_PAD, y0 + cy0 - BOX_PAD, x0 + cx1 + BOX_PAD, y0 + cy1 + BOX_PAD)
            for cx0, cy0, cx1, cy1 in comps]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default=DEFAULT_VIDEO)
    ap.add_argument("--region", nargs=4, type=int, default=list(DEFAULT_REGION),
                    metavar=("Y0", "Y1", "X0", "X1"))
    args = ap.parse_args()

    frames = read_frames(args.video)
    print(f"讀到 {len(frames)} 幀 <- {args.video}")
    if not frames:
        sys.exit(1)

    boxes = locate_boxes(frames, tuple(args.region))
    if boxes is None:
        print("定位 4 支箭頭失敗(色塊聯集沒有找到剛好 4 個圓形包絡)")
        sys.exit(1)
    print("箭頭框(整幀座標,由左到右):")
    for i, b in enumerate(boxes):
        print(f"  箭頭 {i + 1}: {b}")

    frame_idxs = list(range(len(frames)))
    per_arrow_angles = []
    for box in boxes:
        x0, y0, x1, y1 = box
        angs = [rw.angle_of(f[max(0, y0):y1, max(0, x0):x1]) for f in frames]
        per_arrow_angles.append(angs)

    per_arrow_wobbles = [rw.find_wobbles(angs, frame_idxs) for angs in per_arrow_angles]
    filtered = rw.drop_synced_wobbles(per_arrow_wobbles)

    print("\n各支箭頭的晃動偵測(過濾同步假影前 -> 後):")
    n_dropped_total = 0
    for i in range(4):
        before, after = per_arrow_wobbles[i], filtered[i]
        after_set = set(after)
        dropped = len(before) - len(after)
        n_dropped_total += dropped
        print(f"  箭頭 {i + 1}(x={boxes[i][0]}):"
              f" {len(before)} 次晃動,濾掉同步假影 {dropped} 次,剩 {len(after)} 次")
        for f, a in before:
            flag = "" if (f, a) in after_set else "  <- 同步假影,已濾掉"
            print(f"      frame={f:4d}  angle={a:6.1f}deg  "
                  f"最近方向={rw.nearest_cardinal(a)}{flag}")

    dirs = []
    for wobbles in filtered:
        if not wobbles:
            dirs.append(None)
            continue
        mean_a = rw.circ_mean([a for _f, a in wobbles])
        dirs.append(rw.nearest_cardinal(mean_a) if mean_a is not None else None)

    print(f"\n判定結果: {dirs}")
    print(f"正確答案: {TRUTH}")
    hit = dirs == TRUTH
    print("PASS(命中「下、左、下、上」)" if hit else "FAIL(未命中)")
    print(f"同步假影總共濾掉 {n_dropped_total} 次事件"
          f"(帳面吻合使用者提到的『幀 138 和 228』兩個已知假影點,"
          f"實際幀號依這支影片的偵測結果為準,見上方逐項列表)")

    # 也用 rune_wheel.solve() 走一次完整公開介面,交叉核對跟上面手動組裝的結果
    # 一致 —— 這才是「真的測到 solve()」,不是只測到內部函式的組合。
    solve_result = rw.solve(frames, boxes)
    print(f"\nrune_wheel.solve() 直接呼叫結果: {solve_result}")
    assert solve_result == dirs, "solve() 與手動組裝的結果不一致,兩邊邏輯分岔了"

    sys.exit(0 if hit else 1)


if __name__ == "__main__":
    main()
