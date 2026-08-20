"""把訓練好的菲歐娜蘑菇偵測器放到【我們自己的畫面】上量,並且一定要畫出來看。

【為什麼一定要畫圖】2026-08-20 踩過:用模板比對量「恰好 4 框且分屬 4 槽」得到
100%,畫出來才發現框全落在【空舞台】上(分數 0.89),比對到的是聚光燈漸層。
指標可以自我實現,圖不會騙人。所以這支腳本【預設就會輸出標註圖】,不能只看數字。

【為什麼指標是「恰好 4 框」而不是 mAP】先前那次 val mAP50=0.995、實機卻只有
29.6%,量的是不同的東西。下游(指派/追蹤)要的是「這一幀有沒有給我四個框」,
少一個就沒東西可以配對。mAP 高但四框率低,對 production 沒有意義。

用法:
    # 對自己採集的輪次(fiona_collect/NNNN/bands.npz)
    venv-detr/Scripts/python.exe tools/eval_fiona_detr.py --src fiona_collect --viz 8
    # 對任意圖片資料夾
    venv-detr/Scripts/python.exe tools/eval_fiona_detr.py --src some/dir --viz 8

【灰階帶會有域差,如實記著】模型是用彩色的整個謎題視窗訓的;fiona_collect 在
2026-08-20 之前存的是【灰階的窄帶】,兩者不同。那些舊資料量出來的數字只能當
下限參考,真正的驗收要用改版後採集的彩色資料。
"""
import argparse
import glob
import os
import sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models", "fiona_detr", "final")


def load_model(model_dir, device):
    from transformers import RTDetrForObjectDetection, RTDetrImageProcessor
    model = RTDetrForObjectDetection.from_pretrained(model_dir).to(device).eval()
    processor = RTDetrImageProcessor.from_pretrained(model_dir)
    return model, processor


@torch.no_grad()
def detect(model, processor, bgr, device, thr):
    """回 [(x0, y0, x1, y1, score, label), ...],依 x 由左到右。"""
    import cv2
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) if bgr.ndim == 3 else \
        cv2.cvtColor(bgr, cv2.COLOR_GRAY2RGB)
    enc = processor(images=[rgb], return_tensors="pt").to(device)
    out = model(**enc)
    h, w = bgr.shape[:2]
    res = processor.post_process_object_detection(
        out, target_sizes=torch.tensor([[h, w]]).to(device), threshold=thr)[0]
    boxes = []
    for score, label, box in zip(res["scores"], res["labels"], res["boxes"]):
        x0, y0, x1, y1 = [float(v) for v in box]
        boxes.append((x0, y0, x1, y1, float(score), int(label)))
    boxes.sort(key=lambda b: b[0])
    return boxes


def iter_frames(src):
    """回 (名稱, BGR 影像) 的產生器。支援 fiona_collect 目錄與一般圖片資料夾。"""
    import cv2
    npzs = sorted(glob.glob(os.path.join(src, "*", "bands.npz")))
    if npzs:
        for p in npzs:
            bands = np.load(p)["bands"]
            # 只取有代表性的幾幀,不是每一幀都要(一輪好幾百幀)
            idxs = np.linspace(0, len(bands) - 1, min(len(bands), 25)).astype(int)
            for i in idxs:
                b = bands[i]
                yield f"{os.path.basename(os.path.dirname(p))}#{i}", \
                    b if b.ndim == 3 else cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
        return
    for p in sorted(glob.glob(os.path.join(src, "**", "*.jpg"), recursive=True) +
                    glob.glob(os.path.join(src, "**", "*.png"), recursive=True)):
        im = cv2.imread(p)
        if im is not None:
            yield os.path.basename(p), im


def main():
    import cv2
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--src", default=os.path.join(ROOT, "fiona_collect"))
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--viz", type=int, default=8, help="輸出幾張標註圖(0=不輸出,不建議)")
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "fiona_eval"))
    args = ap.parse_args()

    if not os.path.isdir(args.model):
        print(f"!!! 找不到模型 {args.model}(訓練還沒跑完?)")
        sys.exit(1)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, processor = load_model(args.model, device)
    os.makedirs(args.out, exist_ok=True)

    n = four = 0
    counts = {}
    scores = []
    shots = []
    for name, bgr in iter_frames(args.src):
        boxes = detect(model, processor, bgr, device, args.thr)
        n += 1
        k = min(len(boxes), 7)
        counts[k] = counts.get(k, 0) + 1
        scores += [b[4] for b in boxes]
        if len(boxes) == 4:
            four += 1
        if len(shots) < args.viz:
            vis = bgr.copy()
            for x0, y0, x1, y1, sc, lb in boxes:
                col = (0, 0, 255) if lb == 1 else (0, 200, 255)   # princess=紅 plain=黃
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), col, 2)
                cv2.putText(vis, f"{sc:.2f}", (int(x0) + 2, max(12, int(y0) - 3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
            cv2.putText(vis, f"{name}  {len(boxes)}框", (4, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            shots.append(vis)

    if not n:
        print(f"!!! {args.src} 沒有可讀的影像")
        sys.exit(1)
    print(f"來源 {args.src}  共 {n} 幀  門檻 {args.thr}")
    print("每幀框數分布: " + "  ".join(
        f"{k}框={counts.get(k, 0)}({100 * counts.get(k, 0) / n:.0f}%)" for k in range(6)))
    print(f"【恰好 4 框】{four}/{n} = {100 * four / n:.1f}%")
    if scores:
        print(f"分數 中位={np.median(scores):.3f}  min={min(scores):.3f}  max={max(scores):.3f}")

    if shots:
        h = max(s.shape[0] for s in shots)
        w = max(s.shape[1] for s in shots)
        pad = [cv2.copyMakeBorder(s, 0, h - s.shape[0], 0, w - s.shape[1],
                                  cv2.BORDER_CONSTANT, value=(0, 0, 0)) for s in shots]
        grid = np.vstack(pad)
        p = os.path.join(args.out, "eval_boxes.png")
        cv2.imwrite(p, grid)
        print(f"標註圖 → {p}  ← 【一定要打開看】數字會騙人,框畫在哪不會")


if __name__ == "__main__":
    main()
