"""把訓練完的 RT-DETR 匯出成 ONNX,並存下 PyTorch 端的參考輸出供對照用。

背景:伺服器是 CPU + onnxruntime,沒有 torch、沒接 GPU。要判斷這條線能不能上線,
先決條件是「能不能匯出成 ONNX,結果跟 PyTorch 一致」。這支腳本只做匯出 + 存參考
輸出,在有 torch 的環境(venv-detr)跑;真正的一致性比對 + CPU 推論耗時,交給
tools/bench_rune_detr_onnx_cpu.py 在只有 onnxruntime、沒有 torch 的環境(venv,
與伺服器同一個 venv)跑 —— 這樣「CPU 推論耗時」量到的才是伺服器實際會遇到的
情況,不是 torch 幫忙暖過機的環境。

匯出對象:只匯出 model(pixel_values)-> (logits, pred_boxes) 這段(RT-DETR 的
forward 在 backbone/encoder/decoder 全部走完之後回傳的原始輸出)。threshold 篩選
+ xyxy 轉換等後處理是 RTDetrImageProcessor.post_process_object_detection 做的,
純 numpy 運算,量級遠小於模型 forward,沒有 ONNX 化的必要,直接在
tools/eval_rune_geom.py / tools/rune_geom_complete.py 裡重用既有邏輯即可。

用法:
    venv-detr/Scripts/python.exe tools/export_rune_detr_onnx.py
"""
import argparse
import os
import sys

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_cv  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_rune_detr import DS_DIR, load_split  # noqa: E402

MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "rune_detr", "final")


class _ExportWrapper(torch.nn.Module):
    """把 RTDetrObjectDetectionOutput 壓成 (logits, pred_boxes) 兩個 tensor 的
    plain tuple —— torch.onnx.export 不接受自訂 dataclass 輸出。"""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, pixel_values):
        out = self.model(pixel_values=pixel_values)
        return out.logits, out.pred_boxes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=MODEL_DIR)
    ap.add_argument("--out", default=os.path.join(MODEL_DIR, "model.onnx"))
    ap.add_argument("--n-ref-images", type=int, default=8,
                    help="存幾張圖的 PyTorch 參考輸出,供 CPU 端比對誤差用。")
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--img-height", type=int, default=None,
                    help="匯出用的假輸入高度。預設讀 preprocessor_config.json 的"
                         "size.height,不用手動指定——squash(640x640)與 aspect"
                         "(384x1344)模型的 processor 各自記著自己訓練時用的尺寸,"
                         "寫死 640x640 只對 squash 剛好蒙對,對 aspect 會匯出錯誤的"
                         "圖(輸入尺寸與訓練/推論不一致)。")
    ap.add_argument("--img-width", type=int, default=None,
                    help="匯出用的假輸入寬度,同 --img-height。")
    ap.add_argument("--dynamo", action="store_true",
                    help="用新版(torch.export 為基礎)的匯出器。預設關閉、走舊版"
                         "TorchScript-based 匯出器——新版會把 opset 自動升到"
                         "18(Resize 沒有降版轉換器)且不影響 Cos 節點的 int64 輸入"
                         "問題,舊版行為更貼近既有已驗證過的 squash 匯出流程。")
    args = ap.parse_args()

    from transformers import RTDetrForObjectDetection, RTDetrImageProcessor
    print(f"讀取 {args.model_dir}")
    model = RTDetrForObjectDetection.from_pretrained(args.model_dir)
    processor = RTDetrImageProcessor.from_pretrained(args.model_dir)
    model.eval()

    wrapper = _ExportWrapper(model).eval()
    img_h = args.img_height or processor.size["height"]
    img_w = args.img_width or processor.size["width"]
    print(f"匯出輸入尺寸 {img_h}x{img_w}(來自 --img-height/--img-width 或 "
          f"processor.size)")
    dummy = torch.randn(1, 3, img_h, img_w)

    print(f"匯出 ONNX 到 {args.out}(opset {args.opset}, dynamo={args.dynamo})")
    torch.onnx.export(
        wrapper, (dummy,), args.out,
        input_names=["pixel_values"], output_names=["logits", "pred_boxes"],
        dynamic_axes={"pixel_values": {0: "batch"},
                      "logits": {0: "batch"}, "pred_boxes": {0: "batch"}},
        opset_version=args.opset, do_constant_folding=True, dynamo=args.dynamo)
    size_mb = os.path.getsize(args.out) / 1024 / 1024
    print(f"匯出完成,檔案大小 {size_mb:.1f} MB")

    # ---------- 存幾張驗證圖的 pixel_values + PyTorch 參考輸出 ----------
    _, val_recs = load_split()
    ref_dir = os.path.join(os.path.dirname(args.out), "onnx_ref")
    os.makedirs(ref_dir, exist_ok=True)
    n = min(args.n_ref_images, len(val_recs))
    with torch.no_grad():
        for i in range(n):
            rec = val_recs[i]
            bgr = rune_cv.imread(os.path.join(DS_DIR, rec["file"]))
            rgb = bgr[:, :, ::-1]
            img = Image.fromarray(np.ascontiguousarray(rgb))
            enc = processor(images=[img], return_tensors="pt")
            pixel_values = enc["pixel_values"]
            logits, pred_boxes = wrapper(pixel_values)
            # 【bugfix】np.savez(file, ...) 的第一個位置參數本身就叫 file,原本的
            # file=rec["file"] 關鍵字參數會撞名報 TypeError,一路沒被跑到過。改名
            # 成 src_file 避免碰撞(bench_rune_detr_onnx_cpu.py 讀取處要同步改)。
            np.savez(os.path.join(ref_dir, f"ref_{i}.npz"),
                    pixel_values=pixel_values.numpy(),
                    logits=logits.numpy(), pred_boxes=pred_boxes.numpy(),
                    src_file=rec["file"])
    print(f"存了 {n} 張參考輸出到 {ref_dir}(供 tools/bench_rune_detr_onnx_cpu.py 比對)")


if __name__ == "__main__":
    main()
