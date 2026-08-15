"""符文箭頭方向的 CNN 判讀器(ONNX 推論)。

【它取代的是什麼】不是判向器,是【色度分割】。實測:膠囊定位零誤差、模板判向在
乾淨輸入上 97.5%,而整體只有 41% —— 差距全部來自 _chroma_map/_seg 在箭頭被怪物、
技能特效、地形疊到時把背景一起吃進去。分割是手寫幾何規則最不擅長的那一段。

【前處理只能有一份】訓練腳本 import 這裡的 preprocess。train/serve 各寫一份是這類
專案最經典的 bug:不會報錯,只會讓分數莫名其妙掉一截。
"""
import os

import cv2
import numpy as np

import paths

# 前四項與 rune_cv._TPL_DIRS 同序(順時針)。rot90 增強依賴這個順序,不要重排。
CLASSES = ["up", "right", "down", "left", "none"]
IMG = 64


def preprocess(crop_bgr):
    """單格 BGR 小圖 → (3, IMG, IMG) float32,值域 [0,1],通道順序 RGB。"""
    r = cv2.resize(crop_bgr, (IMG, IMG), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(r, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(
        (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1))


def preprocess_batch(crops):
    """多格一次送。回 (N, 3, IMG, IMG) float32。"""
    if not crops:
        return np.zeros((0, 3, IMG, IMG), np.float32)
    return np.stack([preprocess(c) for c in crops]).astype(np.float32)


MODEL_PATH = paths.srv_res("rune_arrow.onnx")
# 守門門檻。tools/calib_arrow_threshold.py 在全部 352 筆正樣本(含訓練集,偏樂觀)
# 上以 0.01 解析度掃過:錯判在 min-prob=0.9733 處消失,0.98 起「其中錯的」= 0
# (82/352 = 23.3% 接受)。往上取一階留一階餘裕給沒見過的畫面 → 0.99(49/352 =
# 13.9% 接受,錯的仍是 0)。
# 【方向要記住】誤判的代價不是漏一次,而是拿雜訊當箭頭去按方向鍵、白燒一次符文
# 冷卻,所以門檻一律往「寧可退線給 2 線」那一側調。
MIN_PROB = 0.99

_sess = None
_sess_tried = False


def _session():
    """惰性建立 ONNX session。載不起來就永久回 None(不重試,免得每幀都在試)。"""
    global _sess, _sess_tried
    if _sess_tried:
        return _sess
    _sess_tried = True
    try:
        import onnxruntime as ort
        if os.path.exists(MODEL_PATH):
            _sess = ort.InferenceSession(
                MODEL_PATH, providers=["CPUExecutionProvider"])
            print(f"[rune_nn] 已載入 {os.path.basename(MODEL_PATH)}")
        else:
            print(f"[rune_nn] 找不到 {MODEL_PATH},退回色度分割")
    except Exception as e:
        print(f"[rune_nn] 載入失敗({e!r}),退回色度分割")
        _sess = None
    return _sess


def available():
    return _session() is not None


def predict(crops):
    """每格 BGR 小圖 → (類別 list, 最高機率 list)。模型不可用回 ([], [])。"""
    sess = _session()
    if sess is None or not crops:
        return [], []
    x = preprocess_batch(crops)
    logits = sess.run(["logits"], {"x": x})[0]
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs = e / e.sum(axis=1, keepdims=True)
    idx = probs.argmax(axis=1)
    return ([CLASSES[i] for i in idx],
            [float(probs[r, i]) for r, i in enumerate(idx)])
