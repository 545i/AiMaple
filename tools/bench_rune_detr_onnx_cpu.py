"""在「沒有 torch、只有 onnxruntime」的環境跑 ONNX 版 RT-DETR:比對跟 PyTorch
輸出的最大誤差,並量 CPU 推論耗時。刻意設計成用 venv(伺服器用的同一個 venv,
CPU only、無 torch)跑,不是 venv-detr —— 這樣量到的數字才是伺服器實際會遇到的
情況,不是 torch 幫忙暖過機、或誤用到 GPU 版 onnxruntime 的數字。

用法:
    venv/Scripts/python.exe tools/bench_rune_detr_onnx_cpu.py
"""
import argparse
import glob
import os
import time

import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models", "rune_detr", "final")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=MODEL_DIR,
                    help="模型資料夾(內含 model.onnx / onnx_ref/)。預設是舊的"
                         "squash 模型;要測 aspect 模型傳 models/rune_detr_ar/final。")
    ap.add_argument("--onnx", default=None)
    ap.add_argument("--ref-dir", default=None)
    ap.add_argument("--n-warmup", type=int, default=3)
    ap.add_argument("--n-bench", type=int, default=30)
    args = ap.parse_args()
    if args.onnx is None:
        args.onnx = os.path.join(args.model_dir, "model.onnx")
    if args.ref_dir is None:
        args.ref_dir = os.path.join(args.model_dir, "onnx_ref")

    print(f"onnxruntime {ort.__version__}, providers 可用: {ort.get_available_providers()}")
    so = ort.SessionOptions()
    # 明確鎖 CPU provider,避免環境裡剛好有 GPU provider 時誤測到 GPU 數字。
    sess = ort.InferenceSession(args.onnx, sess_options=so,
                                providers=["CPUExecutionProvider"])
    print(f"實際使用 provider: {sess.get_providers()}")

    input_name = sess.get_inputs()[0].name

    # ---------- 1. 與 PyTorch 的一致性 ----------
    ref_files = sorted(glob.glob(os.path.join(args.ref_dir, "ref_*.npz")))
    print(f"\n找到 {len(ref_files)} 個 PyTorch 參考輸出,逐一比對 ...")
    max_abs_logits, max_abs_boxes = 0.0, 0.0
    for rf in ref_files:
        d = np.load(rf, allow_pickle=True)
        pixel_values = d["pixel_values"].astype(np.float32)
        logits_ref = d["logits"]
        boxes_ref = d["pred_boxes"]
        logits_onnx, boxes_onnx = sess.run(None, {input_name: pixel_values})
        max_abs_logits = max(max_abs_logits, float(np.abs(logits_onnx - logits_ref).max()))
        max_abs_boxes = max(max_abs_boxes, float(np.abs(boxes_onnx - boxes_ref).max()))
        fname = str(d["src_file"]) if "src_file" in d else rf
        print(f"  {os.path.basename(rf)} ({fname}): "
              f"max|Δlogits| {np.abs(logits_onnx - logits_ref).max():.2e}  "
              f"max|Δboxes| {np.abs(boxes_onnx - boxes_ref).max():.2e}")
    print(f"\n全部參考圖裡最大誤差: logits {max_abs_logits:.2e}  pred_boxes {max_abs_boxes:.2e}")
    tol = 1e-3
    ok = max_abs_logits < tol and max_abs_boxes < tol
    print(f"容差 {tol}: {'PASS' if ok else 'FAIL'}")

    # ---------- 2. CPU 推論耗時 ----------
    # 輸入尺寸直接讀 ONNX 圖本身記的 shape(只有 batch 維是動態的 'batch',
    # H/W 是匯出時就固定死的常數)——squash(640x640)與 aspect(384x1344)
    # 各自的圖形狀不同,寫死 640x640 只對 squash 剛好蒙對。
    in_shape = sess.get_inputs()[0].shape   # ['batch', 3, H, W]
    img_h = in_shape[2] if isinstance(in_shape[2], int) else 640
    img_w = in_shape[3] if isinstance(in_shape[3], int) else 640
    dummy = np.random.randn(1, 3, img_h, img_w).astype(np.float32)
    print(f"\nCPU 推論耗時基準用輸入尺寸: 1x3x{img_h}x{img_w}")
    for _ in range(args.n_warmup):
        sess.run(None, {input_name: dummy})
    timings = []
    for _ in range(args.n_bench):
        t0 = time.perf_counter()
        sess.run(None, {input_name: dummy})
        timings.append(time.perf_counter() - t0)
    t = np.array(timings) * 1000
    print(f"\nCPU 推論耗時(n={args.n_bench}, 已排除 {args.n_warmup} 次暖機):")
    print(f"  平均 {t.mean():.1f}ms  中位數 {np.median(t):.1f}ms  "
          f"min {t.min():.1f}ms  max {t.max():.1f}ms  p90 {np.percentile(t,90):.1f}ms")

    size_mb = os.path.getsize(args.onnx) / 1024 / 1024
    print(f"\nONNX 檔案大小: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
