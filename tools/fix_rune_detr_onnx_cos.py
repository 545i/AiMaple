# -*- coding: utf-8 -*-
"""ONNX 圖手術:修掉 RT-DETR 匯出圖裡 onnxruntime CPU 不支援的 Sin/Cos(double)節點。

背景:`tools/export_rune_detr_onnx.py` 匯出的圖裡,`RTDetrSinePositionEmbedding`
(見 transformers `modeling_rt_detr.py::build_2d_sinusoidal_position_embedding`)
內部全程用 `torch.float64` 算 omega/grid,Sin/Cos 因此吃 double 輸入。onnxruntime
CPU 版只登記了 Sin/Cos 的 float16/float32 kernel,沒有 double,載入時直接報:

    NOT_IMPLEMENTED: Could not find an implementation for Cos(7) node ...

(用 `onnx.shape_inference.infer_shapes(..., data_prop=True)` 查證過,輸入
elem_type=11=DOUBLE,不是任務原先猜測的 INT64——但病灶與修法相同:onnxruntime
CPU 對這兩個節點就是缺對應型別的 kernel,插一個 Cast 到 float32 就繞過去了。)

【為什麼是插 Cast 不是別的路】
  * 修 transformers 原始碼(改 dtype)牽動訓練/HF 生態相容性,不值得。
  * 換匯出器(dynamo=True)測過:opset 被迫升到 18(Resize 沒有降版轉換器)、
    且沒有解決 Sin/Cos 用 double 這件事(病灶在 transformers 建圖邏輯,
    跟走哪個匯出器無關)。
  * 圖手術最小、最可控:只動 4 個節點(1 個 AIFI 層各 2 個:Sin/Cos × H/W),
    其餘全部原樣不動。Sin/Cos 的輸出全部匯入同一個 Concat 再統一 Cast 成
    float32(見圖:Concat_5 -> Cast_4),所以把輸入端提前轉成 float32,
    數值路徑與原圖完全等價,只是提早了一步做精度轉換,精度損失遠低於
    1e-3 的驗收容差(float32 對 sin/cos 這種有界函式的誤差量級 ~1e-7)。

用法:
    venv-detr/Scripts/python.exe tools/fix_rune_detr_onnx_cos.py \
        --onnx models/rune_detr_ar/final/model.onnx
(原地覆寫;也可以用 --out 另存,不動原檔)

跑完之後用 tools/bench_rune_detr_onnx_cpu.py(CPU-only 的 venv)驗證載入成功、
且與 PyTorch 參考輸出的誤差在容差內。
"""
import argparse
import os

import onnx
from onnx import TensorProto, helper, shape_inference


def fix_sincos_double(model: onnx.ModelProto) -> tuple[onnx.ModelProto, int]:
    """對所有輸入是 DOUBLE(或 INT64)的 Sin/Cos 節點,插入 Cast(to=FLOAT) 於其前。
    同一個來源 tensor 若被多個 Sin/Cos 節點共用,只插一個 Cast(用 dict 去重)。
    回 (修好的 model, 插入的 Cast 節點數)。"""
    graph = model.graph
    inferred = shape_inference.infer_shapes(model, check_type=True,
                                            strict_mode=False, data_prop=True)
    dtype_of = {}
    for vi in list(inferred.graph.value_info) + list(inferred.graph.input) + \
              list(inferred.graph.output):
        dtype_of[vi.name] = vi.type.tensor_type.elem_type
    for init in graph.initializer:
        dtype_of[init.name] = init.data_type

    NEEDS_FIX = {TensorProto.DOUBLE, TensorProto.INT64}
    cast_for = {}       # 原始 tensor 名 -> 已插入的 Cast 輸出名(去重用)
    n_fixed = 0

    # 【拓樸序不能亂插】onnx checker 要求節點列表嚴格拓樸排序(用到的 tensor
    # 必須是前面某個節點的輸出)。原圖節點列表本身已經是拓樸排序,所以把新插的
    # Cast 節點放在「第一個用到它的 Sin/Cos 節點」正前方就一定合法——Cast 依賴的
    # src 產生節點必然排在這個 Sin/Cos 節點之前,插在它前面同樣排在 src 產生節點
    # 之後。逐一重建節點列表,而不是事後用 insert(0, ...) 塞到最前面(那樣會把
    # Cast 排到 src 產生節點之前,violate 拓樸序,先前踩過這個坑)。
    new_nodes = []
    for node in graph.node:
        if node.op_type in ("Sin", "Cos"):
            src = node.input[0]
            dt = dtype_of.get(src)
            if dt in NEEDS_FIX:
                if src not in cast_for:
                    cast_out = src + "_as_float32"
                    cast_node = helper.make_node(
                        "Cast", inputs=[src], outputs=[cast_out],
                        name=f"Cast_fix_{len(cast_for)}__{node.name.replace('/', '_')}",
                        to=TensorProto.FLOAT)
                    new_nodes.append(cast_node)
                    cast_for[src] = cast_out
                node.input[0] = cast_for[src]
                n_fixed += 1
        new_nodes.append(node)

    del graph.node[:]
    graph.node.extend(new_nodes)
    return model, n_fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--out", default=None, help="不填就原地覆寫 --onnx")
    args = ap.parse_args()

    print(f"讀取 {args.onnx}")
    model = onnx.load(args.onnx, load_external_data=True)
    model, n_fixed = fix_sincos_double(model)
    if n_fixed == 0:
        print("沒有找到需要修的 Sin/Cos 節點(可能已經修過,或這個圖沒有這個問題)。")
    else:
        print(f"插入 Cast(to=float32)修好 {n_fixed} 個 Sin/Cos 節點")

    out = args.out or args.onnx
    onnx.checker.check_model(model)
    onnx.save(model, out)
    print(f"已存到 {out}")


if __name__ == "__main__":
    main()
