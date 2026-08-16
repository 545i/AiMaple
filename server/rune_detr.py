# -*- coding: utf-8 -*-
"""符文箭頭偵測(RT-DETR + 幾何選擇),取代舊有「find_capsule + 色度分割」定位那段
(判向本身完全不動——這裡只換「怎麼把 4 支箭頭的位置找出來」)。

--------------------------------------------------------------------------
驗收數字(364 張真實樣本,rune_dataset/detr_annotations_full.json,見
.superpowers/detr-aspect.md、.superpowers/detr-select.md 的完整過程紀錄)
--------------------------------------------------------------------------
              現行(find_capsule + 色度分割)   這裡(RT-DETR + 幾何選擇)
    單支正確率        41.0%                          89.8%
    四支全對率        38.4%                          72.8%
    候選不足放棄率      —                              0.3%

判向那段(_chroma_map/_seg/_direction_tpl)完全沒動——框對了它本來就有
97~99.5%,這輪所有增益都來自「框位置更準」。

--------------------------------------------------------------------------
模型
--------------------------------------------------------------------------
用 `models/rune_detr_ar/`(等比輸入 1344x384 訓練的版本),不是 `models/rune_detr/`
(舊的 640x640 squash 版,箭頭被壓扁 3.9 倍,端到端明顯較差,見 detr-aspect.md)。

輸入是【搜尋帶裁切圖】(`rune_cv.search_band()` 那一段,只切 y、不切 x),不是整幀
也不是膠囊裁切圖——這是訓練資料(`rune_dataset/*.png`)本來的樣子(見
`server/rune.py::save_sample` 的存檔邏輯),模型只看過這個尺度的輸入。

--------------------------------------------------------------------------
執行期依賴:torch,優先 GPU
--------------------------------------------------------------------------
優先用 CUDA,不可用才退回 CPU —— 但退回時會【明確印在日誌裡】。靜默降級才是
真正的危險(onnxruntime-gpu 在這台機器上就是指定 CUDA 卻默默用 CPU 跑)。

實測(RTX 5070):GPU 27.8ms/張、CPU 95ms,兩者都遠低於 10~14 秒的謎題窗,
所以退回 CPU 仍然完全可用。GPU 對【旋轉箭頭】才是必要的(0.5 秒停頓內
GPU 約 18 幀、CPU 只有 5 幀)。顯存佔用實測約 430 MiB(權重 186 + 活化 246),
與訓練的 6.4GB 不同量級,不會影響遊戲。

端到端驗證 89.5% / 72.3%
(與下面 ONNX CPU 版量到的 89.6% / 72.3% 一致 —— 前處理/後處理是手刻的 numpy,
與後端無關,換後端不影響數值)。

【為什麼不是 onnxruntime-gpu】試過,在這台 Windows 機器上三種方法都失敗,而且是
【靜默退回 CPU】(providers 回 ['CPUExecutionProvider']):pip 的 nvidia cu12
wheel + PATH、把 CUDA DLL 複製到 provider DLL 同目錄、系統 CUDA 12.9 toolkit
加進 PATH —— 全部卡在 `onnxruntime_providers_cuda.dll` 找不到
`cublasLt64_12.dll`。torch cu128 在同一台機器上現成可用(訓練環境一直在用)。

--------------------------------------------------------------------------
ONNX 路線(保留,目前未使用)
--------------------------------------------------------------------------
下面這段是先前 CPU 版的紀錄,匯出工具與圖手術修法都還留著,之後 onnxruntime 的
GPU 環境理順了可以換回去。`tools/export_rune_detr_onnx.py` 匯出的
圖有 4 個 Sin/Cos 節點吃 float64(RTDetr 的 sinusoidal 位置編碼全程用
torch.float64 算,見 transformers `modeling_rt_detr.py
::build_2d_sinusoidal_position_embedding`),onnxruntime 的 CPU kernel 只登記了
float16/float32,載入直接報 `NOT_IMPLEMENTED: Could not find an implementation
for Cos(7)`。修法是 `tools/fix_rune_detr_onnx_cos.py` 的圖手術:在這 4 個節點前插
Cast(to=float32),數值路徑與原圖等價(4 個 Sin/Cos 輸出全部匯入同一個 Concat 再
統一 Cast 成 float32,提前一步做這件事不影響結果)。修好後在 CPU-only 的 venv
(伺服器同一個環境)驗證過:8 張參考圖裡 7 張與 PyTorch 誤差 <1e-4(容差
1e-3 內),第 8 張有 2 個(共 300 個 query 裡)信心 <0.013 的重複偵測 query 因為
數值上幾乎打平,在 PyTorch/ONNX 之間互換了順序(query 176/177 對調)——這兩個
query 的信心本來就低於 `select_arrows` 的 `min_score=0.015`,不會被選中,對最終
判向沒有影響。CPU 推論實測平均 88.7ms/張(1344x384,已排除 3 次暖機,n=30),
遠低於「<2 秒才可用」的門檻,也比 PyTorch CPU 基準(136ms)快。

--------------------------------------------------------------------------
模型檔放哪裡:可寫素材目錄,不打包進 exe
--------------------------------------------------------------------------
模型(safetensors 77MB / ONNX 77MB)不透過 `paths.srv_res()`(那是內嵌進 exe 的
唯讀資源,會讓 exe 多出 80MB、每次啟動多解壓——`MapleAuto.spec` 開頭註解明寫過
這個代價)。改放 `paths.data_dir("models")/rune_detr_ar/model.onnx`(可寫素材夾,
開發直跑時就是專案根目錄下的 `models/rune_detr_ar/model.onnx`)。模型檔不存在、
onnxruntime 匯入失敗、或 session 建立失敗時,`available()` 永久回 False(不重試,
免得每幀都在試),`rune_cv.read_dirs()` 就會照舊走 `find_capsule +
_read_dirs_chroma`——完全是現行行為,不是新退路。

--------------------------------------------------------------------------
Kill switch
--------------------------------------------------------------------------
預設【開啟】(這次驗收過了,跟先前沒過驗收、預設關閉的 CNN(見 rune_nn.py)不同)。
要整組關掉、退回純現行流程:設環境變數 `MAPLE_RUNE_DETR=0`。
"""
import itertools
import os

import cv2
import numpy as np

import paths

# ---------- Kill switch:預設開啟,設 0 關閉退回現行流程 ----------
ENABLED = os.environ.get("MAPLE_RUNE_DETR", "1") != "0"

# ---------- 模型:可寫素材目錄,不在 exe 裡 ----------
# HuggingFace 格式的權重(safetensors),torch 推論用。
HF_MODEL_DIR = os.path.join(paths.data_dir("models"), "rune_detr_ar", "final")
# ONNX 版保留:匯出工具與圖手術修法都還在(tools/export_rune_detr_onnx.py、
# tools/fix_rune_detr_onnx_cos.py),之後 onnxruntime 的 GPU 環境理順了可以換回去。
MODEL_PATH = os.path.join(paths.data_dir("models"), "rune_detr_ar", "model.onnx")

# 模型訓練時用的輸入尺寸(見 models/rune_detr_ar/final/preprocessor_config.json
# 的 size.height/width)。寫死在這裡是因為執行期沒有 transformers 可讀那個檔案,
# 這兩個數字必須跟匯出時用的尺寸一致,否則等於用錯誤的座標系統餵模型。
IMG_H, IMG_W = 384, 1344


# ==========================================================================
# 幾何選擇規則:窮舉所有 4 框組合,挑「同一列、間距合理、大小相近、分數夠高」
# 的那組。
#
# 【搬自 tools/rune_arrow_select.py,不是重寫】演算法與參數調校過程見
# .superpowers/detr-select.md:先用 246 張圖的人工標註量出 y_std/間距/框寬高的
# 實際分布,再用隨機搜尋(seed=0,4000 組合)在這些統計量附近搜權重與容忍度,
# 目標函式是 squash+aspect 兩個模型的「4 支全選對率」平均;搜尋收斂到約 89.8%
# 後,從並列最佳的前 5 組參數裡,另外直接跑真正的端到端(讀圖 + 既有色度分割/
# 模板判向,不是 proxy 指標)複驗,挑出兩個模型 e2e 四支全對率都最高、且放棄率
# 最低的一組——即下面 SELECT_PARAMS 這組數字,不是憑感覺調的。
# 若要改動選擇邏輯本身,請先改 tools/rune_arrow_select.py 重新跑調參 +
# tools/eval_rune_select.py 複驗,再回頭同步這裡——這裡是執行期用的副本,兩邊
# 不會自動同步。
# ==========================================================================
SELECT_PARAMS = dict(
    min_score=0.015,
    overlap_iou_reject=0.2,
    w_score=20.0,
    sigma_y=10.0,
    sigma_gap=18.0,
    gap_expected=84.0,
    sigma_size=22.0,
    max_candidates_before_prefilter=12,   # 純效能安全閥,不是建模選擇
    prefilter_nms_iou=0.6,
    # 【誤判防線,2026-08 補】原本 select_arrows 只在候選不足 4 個時回 None,
    # 也就是【永遠會從雜訊裡挑出最好的 4 個】,不管那 4 個多爛。實測在沒有謎題的
    # 畫面上照樣回 4 支並判出方向(負樣本 neg0 回 ['left','up','left','left'],
    # err 還是空的)—— 舊的色度分割路徑靠 strict 面積閘門擋掉這種,新路徑把那道
    # 防線弄丟了。代價不是漏一次,是拿背景雜訊去按方向鍵、白燒一次符文冷卻。
    #
    # 門檻怎麼定的:量 120 張正樣本的最佳組合分數,p1=-3.67、p5=4.21、p50=13.65;
    # 兩個負樣本是 -25.94 與 4.61。-10 擋得掉明顯離群的 neg0,而且低於正樣本 p1,
    # 幾乎不會誤擋真符文。
    # 【已知不足】neg1(4.61)比 5% 的真符文分數還高,這道防線擋不掉它 —— 資料集
    # 只有 2 個負樣本,調不出更好的門檻。要真的解決得先收集更多負樣本
    # (畫面上沒有謎題的幀),再重新校準。這是保守的止血,不是完整解法。
    min_goodness=-10.0,
)


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = x1 - x0, y1 - y0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _greedy_nms_trim(boxes, scores, keep_n, iou_thresh):
    """分數高到低貪婪保留,IoU 超過門檻的較低分框先丟,直到 <= keep_n 個或沒得丟。
    只是把候選數壓到窮舉可行的範圍,不是最終的重疊判定(那個在組合評分裡做)。"""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    kept = []
    for i in order:
        if any(_iou(boxes[i], boxes[j]) > iou_thresh for j in kept):
            continue
        kept.append(i)
    if len(kept) >= keep_n:
        return kept[:keep_n]
    kept_set = set(kept)
    for i in order:
        if len(kept) >= keep_n:
            break
        if i not in kept_set:
            kept.append(i)
            kept_set.add(i)
    return kept


def _combo_score(bs, ss, p):
    """bs/ss: 長度剛好 4 的 list(box tuple / score)。回 (goodness, 依 x 排序後的
    bs)或 None(組合內有重疊,直接淘汰)。刻意不用 numpy——對這麼小的陣列,
    numpy 呼叫開銷比計算本身貴一個數量級(調參時實測)。"""
    for i in range(4):
        bi = bs[i]
        for j in range(i + 1, 4):
            if _iou(bi, bs[j]) > p["overlap_iou_reject"]:
                return None

    order = sorted(range(4), key=lambda k: bs[k][0] + bs[k][2])
    bs = [bs[k] for k in order]
    ss = [ss[k] for k in order]

    yc = [(b[1] + b[3]) * 0.5 for b in bs]
    xc = [(b[0] + b[2]) * 0.5 for b in bs]
    ws = [b[2] - b[0] for b in bs]
    hs = [b[3] - b[1] for b in bs]

    y_mean = sum(yc) / 4.0
    y_std = (sum((v - y_mean) ** 2 for v in yc) / 4.0) ** 0.5

    gap_expected = p["gap_expected"]
    gap_dev = (abs(xc[1] - xc[0] - gap_expected)
               + abs(xc[2] - xc[1] - gap_expected)
               + abs(xc[3] - xc[2] - gap_expected)) / 3.0

    w_mean = sum(ws) / 4.0
    w_std = (sum((v - w_mean) ** 2 for v in ws) / 4.0) ** 0.5
    h_mean = sum(hs) / 4.0
    h_std = (sum((v - h_mean) ** 2 for v in hs) / 4.0) ** 0.5
    size_dev = w_std + h_std

    mean_score = sum(ss) / 4.0

    goodness = (
        p["w_score"] * mean_score
        - (y_std / p["sigma_y"]) ** 2
        - (gap_dev / p["sigma_gap"]) ** 2
        - (size_dev / p["sigma_size"]) ** 2
    )
    return goodness, bs


def select_arrows(boxes, scores, params=None):
    """boxes: (N,4) xyxy(list/array 皆可), scores: (N,)。

    回 4 個 (x0,y0,x1,y1) tuple 組成的 list(依 x 中心由左到右排序),或 None
    (候選不足 4 個,或所有組合都因重疊被淘汰——選不出來,回報失敗,由呼叫端
    退回現行 find_capsule + 色度分割流程)。不做任何幾何補全/外插。"""
    p = SELECT_PARAMS if params is None else {**SELECT_PARAMS, **params}
    min_score = p["min_score"]
    boxes_l = [tuple(b) for b, s in zip(boxes, scores) if s >= min_score]
    scores_l = [float(s) for s in scores if s >= min_score]
    n = len(boxes_l)
    if n < 4:
        return None

    if n > p["max_candidates_before_prefilter"]:
        idx = _greedy_nms_trim(boxes_l, scores_l, p["max_candidates_before_prefilter"],
                                p["prefilter_nms_iou"])
        boxes_l = [boxes_l[i] for i in idx]
        scores_l = [scores_l[i] for i in idx]
        n = len(boxes_l)
        if n < 4:
            return None

    best = None
    for idxs in itertools.combinations(range(n), 4):
        bs4 = [boxes_l[i] for i in idxs]
        ss4 = [scores_l[i] for i in idxs]
        res = _combo_score(bs4, ss4, p)
        if res is None:
            continue
        sc, bs_sorted = res
        if best is None or sc > best[0]:
            best = (sc, bs_sorted)
    if best is None:
        return None
    # 誤判防線:最佳組合也得夠好才採用。沒有這道,雜訊畫面照樣會回 4 支
    # (見 SELECT_PARAMS 裡 min_goodness 的說明與量測)。寧可回 None 退給 2 線,
    # 也不要拿背景雜訊去按方向鍵。
    if best[0] < p["min_goodness"]:
        return None
    return best[1]


# ==========================================================================
# 模型載入 + 推論
# ==========================================================================
_sess = None
_sess_tried = False


def _session():
    """惰性載入模型,回 (model, device) 或 None。ENABLED=False 時永遠回 None。
    載不起來也永久回 None(不重試,免得每幀都在試)。

    【優先 GPU,CUDA 不可用才退回 CPU】實測(RTX 5070)GPU 27.8ms、CPU 95ms,
    兩者都遠低於 10~14 秒的謎題窗,所以退回 CPU 仍然完全可用,沒有理由因為沒有
    GPU 就整個停用、白白掉回 41%。

    但退回時會【明確印出來】—— 靜默降級才是真正的危險:onnxruntime-gpu 在這台
    機器上就是指定了 CUDA 卻默默用 CPU 跑,不去查 `get_providers()` 根本不會發現。
    所以這裡不只選裝置,還會把實際用的裝置寫進日誌。

    (GPU 對【旋轉箭頭】才是必要的:那需要在 0.5 秒的停頓內抓多幀做停留分析,
     27.8ms 等於每秒 36 幀、0.5 秒約 18 幀夠用;CPU 95ms 只有每秒 10 幀。)

    【為什麼是 torch 而不是 onnxruntime】原本走 ONNX(CPU 88.7ms,見檔頭)。改成
    強制 GPU 之後試過 onnxruntime-gpu,在這台 Windows 機器上三種方法都失敗、而且
    是【靜默退回 CPU】:pip 的 nvidia cu12 wheel + PATH、把 CUDA DLL 複製到
    provider DLL 同目錄、系統 CUDA 12.9 toolkit 加進 PATH —— 都卡在
    `onnxruntime_providers_cuda.dll` 找不到 `cublasLt64_12.dll`。torch cu128 在
    同一台機器上是現成可用的(訓練環境 venv-detr 一直在用),所以改用它。
    ONNX 模型檔與匯出工具都保留,之後 onnxruntime GPU 環境理順了可以換回去 ——
    前處理/後處理是手刻的 numpy,與後端無關,換回去不必重驗數值。
    """
    global _sess, _sess_tried
    if not ENABLED:
        return None
    if _sess_tried:
        return _sess
    _sess_tried = True
    if not os.path.isdir(HF_MODEL_DIR):
        print(f"[rune_detr] 找不到 {HF_MODEL_DIR},退回 find_capsule+色度分割")
        return None
    try:
        import torch
        from transformers import RTDetrForObjectDetection
        device = where = None
        try:
            # 【不能只看 is_available()】實測 CUDA_VISIBLE_DEVICES="" 時它仍回 True,
            # 但 get_device_name(0) 會拋 AssertionError('Invalid device id')。
            # 只憑 is_available() 判斷會讓整個模組因為例外而停用,而不是退回 CPU。
            if torch.cuda.is_available() and torch.cuda.device_count() > 0:
                name = torch.cuda.get_device_name(0)      # 真的探一次才算數
                device, where = torch.device("cuda"), f"GPU ({name})"
        except Exception as e:
            print(f"[rune_detr] CUDA 探測失敗({e!r}),改用 CPU")
        if device is None:
            # 不是靜默降級:印清楚,免得「以為在用 GPU」而實際上不是。
            device = torch.device("cpu")
            where = "CPU(CUDA 不可用 —— 仍可用,但慢約 3.4 倍;旋轉箭頭功能會不夠快)"
        model = RTDetrForObjectDetection.from_pretrained(HF_MODEL_DIR).to(device).eval()
        _sess = (model, device)
        print(f"[rune_detr] 已載入 {HF_MODEL_DIR} 於 {where}")
    except Exception as e:
        print(f"[rune_detr] 載入失敗({e!r}),退回 find_capsule+色度分割")
        _sess = None
    return _sess


def available():
    return _session() is not None


def _preprocess(band_bgr):
    """搜尋帶裁切圖(BGR)→ (1,3,IMG_H,IMG_W) float32,RGB、[0,1]、CHW。

    對應 `models/rune_detr_ar/final/preprocessor_config.json`:
    do_resize(精確縮到 IMG_H x IMG_W,不保留長寬比——但搜尋帶本身已經很接近這個
    比例,幾乎不扭曲,見 .superpowers/detr-aspect.md)、do_rescale(1/255)、
    do_normalize=False(不減均值/除標準差)。resample=2(bilinear)用
    cv2.INTER_LINEAR 對應——用真正的 8 張驗證圖比對過,與 HF
    RTDetrImageProcessor(torchvision 後端)的輸出平均誤差 <0.001、最大誤差
    <0.03(執行期沒有 torch/PIL,無法用完全相同的實作,這個誤差量級對偵測
    結果沒有可觀察的影響——見本檔案開頭的驗收數字,是拿這套 cv2 前處理量出來的)。
    """
    rgb = cv2.cvtColor(band_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (IMG_W, IMG_H), interpolation=cv2.INTER_LINEAR)
    x = (resized.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    return np.ascontiguousarray(x)


def _postprocess(logits, pred_boxes, band_h, band_w):
    """logits (1,Q,4) / pred_boxes (1,Q,4 cxcywh, 0-1 正規化) → (boxes xyxy 絕對
    像素(band-crop 座標), scores)。

    複製 `RTDetrImageProcessor.post_process_object_detection(use_focal_loss=True,
    threshold=0.0)` 的邏輯(執行期沒有 transformers,手刻等價版):
    sigmoid → 攤平成 (Q*num_classes,) → 取分數最高的 Q 個(query,class)組合
    (RTDetr 用 focal loss,不是每個 query 只留 argmax 類別,可能同一個 query
    的兩個類別都入選、也可能某個 query 完全出局)→ 換算回 query 索引取回對應的
    框 → cxcywh 轉 xyxy → 乘上 band 的實際寬高(模型看到的是縮放後的
    IMG_W x IMG_H,但輸出座標是【正規化 0-1】,乘原圖寬高就換回原圖像素,
    與縮放比例無關)。threshold=0.0 等於不篩,把全部 Q 個結果都交給
    `select_arrows` 自己的 min_score 篩選——這正是驗收時用的流程
    (`tools/eval_rune_detr.py::infer` 也是 threshold=0.0)。"""
    scores2d = 1.0 / (1.0 + np.exp(-logits[0]))       # (Q, C)
    q, c = scores2d.shape
    flat = scores2d.reshape(-1)
    k = min(q, flat.shape[0])
    order = np.argsort(-flat)[:k]
    top_scores = flat[order]
    q_idx = order // c

    b = pred_boxes[0, q_idx]                          # (k,4) cx,cy,w,h
    cx, cy, bw, bh = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    x0 = (cx - bw / 2.0) * band_w
    y0 = (cy - bh / 2.0) * band_h
    x1 = (cx + bw / 2.0) * band_w
    y1 = (cy + bh / 2.0) * band_h
    boxes = np.stack([x0, y0, x1, y1], axis=1)
    return boxes, top_scores


def detect_arrows(frame_bgr):
    """從一張遊戲畫面(完整幀,或已裁切的搜尋帶/膠囊圖)偵測 4 支箭頭。

    回 4 個 (x0,y0,x1,y1)(依 x 由左到右排序,frame_bgr 的座標系)組成的 list,
    或 None(模型不可用 / 候選不足 4 支——由呼叫端 `rune_cv.read_dirs` 退回現行
    find_capsule + 色度分割流程)。

    輸入先用 `rune_cv.search_band()` 切掉不會有膠囊的上下範圍(只切 y、不切
    x)——這正是訓練資料的樣子(`server/rune.py::save_sample` 存進
    `rune_dataset/` 的就是這個裁切),模型只看過這個尺度的輸入,直接餵整幀會
    是分布外輸入。"""
    sess = _session()
    if sess is None:
        return None
    import rune_cv   # 延遲匯入避免與 rune_cv 的模組載入順序產生循環匯入
    h, w = frame_bgr.shape[:2]
    by0, by1 = rune_cv.search_band(h)
    band = frame_bgr[by0:by1]
    if band.size == 0:
        return None
    bh, bw = band.shape[:2]

    import torch
    model, device = sess
    x = _preprocess(band)                     # 手刻 numpy 前處理,與後端無關
    with torch.no_grad():
        out = model(pixel_values=torch.from_numpy(x).to(device))
    # 轉回 numpy 交給同一份手刻後處理 —— 換後端時數值路徑不變,不必重驗
    logits = out.logits.detach().cpu().numpy()
    pred_boxes = out.pred_boxes.detach().cpu().numpy()
    boxes, scores = _postprocess(logits, pred_boxes, bh, bw)

    sel = select_arrows(boxes, scores, SELECT_PARAMS)
    if sel is None:
        return None
    # 換回 frame_bgr 的座標系:band 只切了 y,x 不用還原,y 要加回 by0。
    return [(x0, y0 + by0, x1, y1 + by0) for (x0, y0, x1, y1) in sel]
