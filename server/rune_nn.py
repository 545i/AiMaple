"""符文箭頭方向的 CNN 判讀器(ONNX 推論)。

【它取代的是什麼】不是判向器,是【色度分割】。實測:膠囊定位零誤差、模板判向在
乾淨輸入上 97.5%,而整體只有 41% —— 差距全部來自 _chroma_map/_seg 在箭頭被怪物、
技能特效、地形疊到時把背景一起吃進去。分割是手寫幾何規則最不擅長的那一段。

【前處理只能有一份】訓練腳本 import 這裡的 preprocess。train/serve 各寫一份是這類
專案最經典的 bug:不會報錯,只會讓分數莫名其妙掉一截。
"""
import cv2
import numpy as np

# 前四項與 rune_cv._TPL_DIRS 同序(順時針)。rot90 增強依賴這個順序,不要重排。
CLASSES = ["up", "right", "down", "left", "none"]
IMG = 32


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
