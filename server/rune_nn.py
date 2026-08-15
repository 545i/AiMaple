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

# 【預設關閉 —— 驗收沒過,不是忘了開】2026-08-15 用完整 352 筆資料集(含
# gate_split 過閘門/未過閘門兩組)實測,MIN_PROB=0.99 零誤判門檻下:
#
#            現行色度分割   CNN(IMG=32)   CNN(IMG=64 重訓)   驗收門檻
#   整體單支正確率   41%        15.6%          12.8%         ≥ 90%
#   原本過閘門那組   97.5%      28.8%          22.9%         ≥ 97%
#   負樣本誤報        0          0              0             0
#
# 放寬到 MIN_PROB=0.80 時 CNN 整體可到 61.6%,但會出現 4 筆整組判錯(不符合
# 「負樣本誤報 = 0」的硬性條件)。任一條不過就不上線是計畫明文規定,所以這裡
# 預設關閉,`read_dirs` 全部走 _read_dirs_chroma(現行色度分割)退路。
#
# 要做實驗才手動開:設環境變數 MAPLE_RUNE_NN=1。開啟後預期整體正確率會從
# 41%(色度分割)掉到 15.6%(CNN),不要在正式服務或未經確認的分支上開著跑。
#
# 模型檔、訓練/評估腳本都留著,方向未定,後續要接著做「混合式:舊閘門過的走
# 舊路徑,只把舊閘門擋下的交給 CNN」時還用得上(見 DEV_LOG.md 符文章節)。
ENABLED = os.environ.get("MAPLE_RUNE_NN", "0") == "1"

_sess = None
_sess_tried = False


def _session():
    """惰性建立 ONNX session。ENABLED=False(預設)時永遠回 None。
    載不起來也永久回 None(不重試,免得每幀都在試)。"""
    global _sess, _sess_tried
    if not ENABLED:
        return None
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
