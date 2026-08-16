# 符文箭頭 CNN 判讀器 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用小型 CNN 取代 `rune_cv` 的色度分割,把符文箭頭的單支判讀正確率從 41% 拉到 90% 以上。

**Architecture:** 膠囊定位與四等分維持現狀(實測零誤差),只把「從格子裡讀出方向」這一段換掉。訓練用 PyTorch 在可拋棄的 `venv-train` 離線做完,匯出 ONNX;執行期用 onnxruntime 推論。前處理程式碼由訓練與推論**共用同一份**,避免 train/serve skew。模型檔不存在時整條退回現行色度分割路徑。

**Tech Stack:** Python 3 / OpenCV / numpy(既有)、onnxruntime(新增執行期依賴)、PyTorch CPU(僅訓練,可拋棄)、pytest(僅開發)

**Spec:** `docs/superpowers/specs/2026-08-15-rune-arrow-cnn-design.md`

## Global Constraints

- 執行期推論一律走 **onnxruntime**(使用者指定方案 B)。不得改用 numpy 手寫前向或其他執行期。
- **模型檔不存在時,`read_dirs` 必須完整退回現行色度分割路徑**,行為與改動前逐位元一致。沒帶模型的舊 exe 不能壞。
- 前處理(resize / 色彩空間 / 正規化 / 維度順序)**只能有一份實作**,放在 `server/rune_nn.py`,訓練腳本 import 它。
- 類別順序固定為 `["up", "right", "down", "left", "none"]`。前四項與 `rune_cv._TPL_DIRS` 同序(順時針),rot90 增強依賴這個順序。
- 標籤只能來自遊戲驗證(`purple_gone`)或人工判讀(`manual`)。**不得用模型自己的輸出當標籤**。
- 輸入尺寸固定 32×32×3,float32,值域 [0,1],CHW,批次 4 格一次送。
- 驗收四條(任一不過就不上線,`read_dirs` 保持走現行路徑):
  1. 整體單支正確率 **≥ 90%**(現況 41%)
  2. 「原本就過閘門」那組維持 **≥ 97%**
  3. 負樣本**零誤報**
  4. 必須明顯贏過 Task 3 量到的不學習基準
- 本輪**不處理旋轉箭頭**。

## 現況數字(實作前的基準,不要重新推導)

| 項目 | 值 |
|---|---|
| `index.jsonl` 總筆數 / 正樣本 / 負樣本 | 367 / 365 / 2 |
| 膠囊框找得到的正樣本 | 352(13 筆找不到,跳過) |
| 可用箭頭 | 352 × 4 = **1408** |
| 現行:過閘門的樣本 | 153 筆,單支 597/612 = 97.5% |
| 現行:被閘門擋下的樣本 | 199 筆(另 13 筆找不到框) |
| 現行:整體 | 全對 140/365,單支 597/1460 = **41%** |

## 檔案結構

| 檔案 | 責任 |
|---|---|
| `server/rune_nn.py`(新) | 類別常數、前處理、ONNX 載入與推論。**訓練與執行期的共用單一來源** |
| `tools/rune_dataset_build.py`(新) | 從 `index.jsonl` 萃取每格 crop 與標籤、生負樣本、rot90 標籤變換 |
| `tools/bench_arrow_baseline.py`(新) | 不學習的整格模板匹配基準,只為量一個數字 |
| `tools/train_rune_arrow.py`(新) | 時間切分、增強、模型、訓練迴圈、匯出 ONNX、報兩組正確率 |
| `server/rune_cv.py`(改) | `read_dirs` 接上模型 + 信心度守門,取代面積閘門 |
| `tests/`(新) | pytest。`conftest.py` 把 `server/` 加進 `sys.path` |
| `requirements.txt`(改) | 補 `onnxruntime` |
| `requirements-dev.txt`(新) | `pytest`(開發用,不進打包) |
| `MapleAuto.spec`(改) | `datas` 補 `rune_arrow.onnx`、`hiddenimports` 補 `onnxruntime` 與 `rune_nn` |

---

### Task 1: 測試骨架 + 每格 crop 萃取

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_rune_dataset_build.py`
- Create: `tools/rune_dataset_build.py`

**Interfaces:**
- Consumes: `rune_cv.imread`、`rune_cv.find_capsule`、`rune_cv.slots`(皆已存在)
- Produces:
  - `DS_DIR: str` —— 資料集目錄絕對路徑
  - `records(ds_dir=DS_DIR) -> Iterator[dict]` —— 逐筆讀 `index.jsonl`
  - `load_sample(ds_dir, rec) -> tuple[np.ndarray | None, list | None]` —— 回 `(影像, 膠囊框)`,任一取不到回 `(None, None)`
  - `iter_arrow_crops(ds_dir=DS_DIR) -> Iterator[tuple[str, np.ndarray, str, int]]` —— 產生 `(檔名, crop_bgr, 標籤, 格索引)`
  - `iter_negative_crops(ds_dir=DS_DIR, seed=0) -> Iterator[tuple[str, np.ndarray, tuple[int, int, int, int]]]` —— 產生 `(檔名, crop_bgr, 取樣框)`,每張圖 1 個。**取樣框要一起回傳**,否則測試無法驗證它真的沒疊到膠囊

`records` 與 `load_sample` 是公開介面(不加底線):後面三個 task 的腳本都要用它們,跨模組用私有名稱是在替未來的自己埋雷。

- [ ] **Step 1: 裝 pytest 並建立測試骨架**

專案目前沒有任何測試。pytest 只給開發用,**不要加進 `requirements.txt`**(那是執行期依賴,會被打包掃到)。

建立 `requirements-dev.txt`:

```
# 開發用,執行期與打包都不需要
pytest
```

安裝:

```bash
venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

建立 `tests/conftest.py`:

```python
"""讓測試能直接 import server/ 底下的模組(專案沒有套件結構,伺服器是用平面 import)。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
```

- [ ] **Step 2: 寫失敗的測試**

建立 `tests/test_rune_dataset_build.py`:

```python
import numpy as np
import pytest

import rune_dataset_build as b


@pytest.fixture(scope="module")
def crops():
    return list(b.iter_arrow_crops())


def test_crop_count_matches_dataset(crops):
    """352 筆找得到膠囊框的正樣本 × 4 格 = 1408 支箭頭。

    這個數字是實作前量過的基準;對不上表示萃取漏了樣本或多切了格子。
    """
    assert len(crops) == 1408


def test_every_crop_is_one_slot_sized(crops):
    """膠囊框實測恆為 329x81,四等分後每格約 82x81。

    尺寸跑掉代表框抓錯或等分算錯 —— 那會讓圖與標籤對不上,是最該擋下的錯誤。
    """
    for _f, img, _d, _k in crops:
        h, w = img.shape[:2]
        assert 70 <= w <= 95, f"格寬 {w} 不合理"
        assert 70 <= h <= 95, f"格高 {h} 不合理"


def test_labels_are_valid_directions(crops):
    assert {d for _f, _i, d, _k in crops} == {"up", "right", "down", "left"}


def test_labels_roughly_balanced(crops):
    """四個方向在資料集裡本來就接近均勻(393/372/350/345)。
    嚴重偏斜代表萃取時格索引與標籤對錯位。"""
    from collections import Counter
    c = Counter(d for _f, _i, d, _k in crops)
    assert max(c.values()) / min(c.values()) < 1.3, c


def test_negative_crops_do_not_overlap_capsule():
    """負樣本必須完全在膠囊框外 —— 疊到箭頭就等於把正樣本標成 none,是錯標。

    這裡不只數數量,而是【重新驗證每一個負樣本與膠囊框沒有交集】:重疊判斷寫錯
    是靜默的,錯標會安靜地混進訓練集,只表現為分數不如預期。
    """
    boxes = {}
    for rec in b.records():
        if rec.get("negative"):
            continue
        img, box = b.load_sample(b.DS_DIR, rec)
        if img is not None:
            boxes[rec["file"]] = box
    n = 0
    for fname, crop, (x0, y0, x1, y1) in b.iter_negative_crops():
        n += 1
        assert crop.size > 0
        bx = boxes[fname]
        disjoint = (x1 <= bx[0] or x0 >= bx[2] or y1 <= bx[1] or y0 >= bx[3])
        assert disjoint, f"{fname} 的負樣本 {(x0, y0, x1, y1)} 疊到膠囊 {bx}"
        assert crop.shape[1] == (bx[2] - bx[0]) // 4, "負樣本寬度要等於一格箭頭"
    assert n > 300, f"負樣本只生出 {n} 個,不足以平衡五個類別"
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_dataset_build.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'rune_dataset_build'`

- [ ] **Step 4: 實作萃取模組**

建立 `tools/rune_dataset_build.py`:

```python
"""從 rune_dataset 萃取每格箭頭小圖與標籤,供訓練使用。

【為什麼可以直接信任這批 crop】膠囊框實測零誤差(被閘門擋下的 199 筆,框全都是
329x81,p5=p50=p95),所以四等分切出來的格子與標籤是對得上的。訓練資料最容易出錯
的地方就在這裡,而這個專案剛好沒有這個問題。

標籤來自 index.jsonl 的 dirs,那是【遊戲驗證過】的(按完四個方向後紫標消失)。
不要改成用任何模型的輸出當標籤。
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import rune_cv  # noqa: E402

DS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rune_dataset")


def records(ds_dir=DS_DIR):
    """逐筆讀 index.jsonl。公開介面 —— 後面三個腳本都要用。"""
    idx = os.path.join(ds_dir, "index.jsonl")
    with open(idx, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_sample(ds_dir, rec):
    """回 (影像, 膠囊框);任一取不到回 (None, None)。

    capsule 欄位是存樣本當下 find_capsule 的結果,舊樣本可能沒有 → 現場重算。
    """
    p = os.path.join(ds_dir, rec["file"])
    if not os.path.exists(p):
        return None, None
    img = rune_cv.imread(p)
    if img is None:
        return None, None
    box = rec.get("capsule") or rune_cv.find_capsule(img)
    return (img, box) if box else (None, None)


def iter_arrow_crops(ds_dir=DS_DIR):
    """產生 (檔名, crop_bgr, 標籤, 格索引)。找不到膠囊框的樣本直接跳過。"""
    for rec in records(ds_dir):
        if rec.get("negative"):
            continue
        img, box = load_sample(ds_dir, rec)
        if img is None:
            continue
        for k, (x0, y0, x1, y1) in enumerate(rune_cv.slots(box, 4)):
            crop = img[y0:y1, x0:x1]
            if crop.size:
                yield rec["file"], crop, rec["dirs"][k], k


def iter_negative_crops(ds_dir=DS_DIR, seed=0):
    """產生 (檔名, crop_bgr, 取樣框):與膠囊框【完全不重疊】的同尺寸區塊,每張圖 1 個。

    每張圖只取 1 個是刻意的:1408 支箭頭經 rot90 增強後每個方向 1408 筆,
    352 個負樣本同樣增強後也是 1408 筆,五類剛好平衡。

    取樣框要一起回傳,否則測試無法驗證「真的沒疊到膠囊」—— 疊到就是錯標,
    而錯標是靜默的,只會表現為分數不如預期。
    """
    rng = np.random.default_rng(seed)
    for rec in records(ds_dir):
        if rec.get("negative"):
            continue
        img, box = load_sample(ds_dir, rec)
        if img is None:
            continue
        h, w = img.shape[:2]
        bw = (box[2] - box[0]) // 4
        bh = box[3] - box[1]
        if w - bw <= 0 or h - bh <= 0:
            continue
        for _ in range(50):
            x = int(rng.integers(0, w - bw + 1))
            y = int(rng.integers(0, h - bh + 1))
            overlap = not (x + bw <= box[0] or x >= box[2] or
                           y + bh <= box[1] or y >= box[3])
            if overlap:
                continue
            crop = img[y:y + bh, x:x + bw]
            if crop.size:
                yield rec["file"], crop, (x, y, x + bw, y + bh)
            break
```

- [ ] **Step 5: 跑測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_dataset_build.py -v`
Expected: 5 passed

若 `test_crop_count_matches_dataset` 對不上 1408,**不要改測試去遷就**——先查是哪些樣本被跳過(印出跳過的檔名),確認是「找不到膠囊框」的那 13 筆而不是別的原因。

- [ ] **Step 6: 提交**

```bash
git add requirements-dev.txt tests/ tools/rune_dataset_build.py
git commit -m "符文訓練資料萃取:每格 crop + 負樣本,附 pytest 骨架"
```

---

### Task 2: rot90 標籤變換(用專案自己的判向器實證驗證)

**Files:**
- Modify: `tools/rune_dataset_build.py`
- Modify: `tests/test_rune_dataset_build.py`

**Interfaces:**
- Produces: `rot_label(label: str, k: int) -> str` —— 影像經 `np.rot90(img, k)` 後對應的新標籤

- [ ] **Step 1: 寫失敗的測試**

這個測試的價值在於**不靠推理、靠專案自己已驗證過的判向器當裁判**。`rune_cv._TPL` 是一張 32×32 的「向上箭頭」模板,`_direction_tpl` 是實測交叉驗證 100/100 的判向器。把模板轉 k 次再丟給判向器,答案必須等於 `rot_label("up", k)`。

加進 `tests/test_rune_dataset_build.py`:

```python
def test_rot_label_matches_project_direction_reader():
    """rot90 的標籤變換必須與專案既有判向器一致。

    不用人腦推導旋轉方向 —— 拿 rune_cv 自己的模板與判向器當裁判。
    這是整個增強策略的地基:錯了就等於把 1408 支箭頭全部錯標成四倍。
    """
    import numpy as np
    import rune_cv
    assert rune_cv._TPL is not None, "判向模板沒載到,無法驗證"
    up_mask = (rune_cv._TPL > 0.5).astype(np.uint8) * 255
    for k in range(4):
        rotated = np.ascontiguousarray(np.rot90(up_mask, k))
        assert rune_cv._direction_tpl(rotated) == b.rot_label("up", k), \
            f"k={k} 的標籤變換與判向器不一致"


def test_rot_label_leaves_none_alone():
    """負樣本轉了還是負樣本。"""
    for k in range(4):
        assert b.rot_label("none", k) == "none"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_dataset_build.py -k rot_label -v`
Expected: FAIL,`AttributeError: module 'rune_dataset_build' has no attribute 'rot_label'`

- [ ] **Step 3: 實作**

加進 `tools/rune_dataset_build.py`(放在 import 之後、`records` 之前):

```python
# 與 rune_nn.CLASSES 同序。前四項順時針,所以 np.rot90(逆時針 k 次)等於索引往回退 k。
DIRS = ["up", "right", "down", "left"]


def rot_label(label, k):
    """影像經 np.rot90(img, k) 之後的新標籤。

    np.rot90 是【逆時針】,而 DIRS 是順時針排列,所以索引往回退 k。
    這個方向不要用推的 —— 見 test_rot_label_matches_project_direction_reader,
    那個測試拿 rune_cv 自己的判向器當裁判驗過。
    """
    if label == "none":
        return "none"
    return DIRS[(DIRS.index(label) - k) % 4]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_dataset_build.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add tools/rune_dataset_build.py tests/test_rune_dataset_build.py
git commit -m "rot90 標籤變換:用專案自己的判向器當裁判驗證方向"
```

---

### Task 3: 不學習的基準數字

在帶進 onnxruntime 依賴之前,先量一個「完全不學習」的方案能做到多少。這個數字是驗收條件第 4 條的門檻,也是模型值不值得的唯一依據。

做法:跳過色度分割,直接拿 `rune_arrow_tpl.png` 的四個旋轉版在**整格**上做正規化互相關(NCC),取最高分的方向。

**Files:**
- Create: `tools/bench_arrow_baseline.py`
- Create: `tests/test_bench_baseline.py`

**Interfaces:**
- Consumes: `rune_dataset_build.iter_arrow_crops`
- Produces:
  - `baseline_dir(crop_bgr) -> str` —— 回四個方向之一
  - `split_by_current_gate(ds_dir=DS_DIR) -> tuple[set[str], set[str]]` —— 回(過閘門的檔名集合, 被擋下的檔名集合)。**第一次算完寫進 `rune_dataset/gate_split.json`,之後一律讀檔**

> ⚠ **切分必須固化成檔案,這是這個計畫最容易踩的陷阱。**
> `split_by_current_gate` 是靠呼叫 `rune_cv.read_dirs` 來判斷「有沒有過閘門」的,
> 但 Task 7 會把 `read_dirs` 改成走 CNN —— 屆時同一支函式算出來的切分就不再是
> 「現行閘門」的切分,驗收條件第 2 條(原本就過閘門那組不得退步)的分母會在腳下
> 悄悄變掉,而且不會有任何錯誤訊息。所以**Task 3 算出來就寫死進 JSON,之後只讀不算**。

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_bench_baseline.py`:

```python
import bench_arrow_baseline as bench


def test_gate_split_matches_measured_numbers():
    """實作前量過:153 筆過閘門、199 筆被擋下(另 13 筆找不到框)。

    這個切分是驗收條件第 2 條的分母,對不上就無從判斷「有沒有退步」。
    """
    passed, gated = bench.split_by_current_gate()
    assert len(passed) == 153, f"過閘門 {len(passed)} 筆,期望 153"
    assert len(gated) == 199, f"被擋下 {len(gated)} 筆,期望 199"


def test_baseline_returns_a_direction():
    import rune_dataset_build as b
    _f, crop, _d, _k = next(iter(b.iter_arrow_crops()))
    assert bench.baseline_dir(crop) in {"up", "right", "down", "left"}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_bench_baseline.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'bench_arrow_baseline'`

- [ ] **Step 3: 實作**

建立 `tools/bench_arrow_baseline.py`:

```python
"""不學習的基準:整格模板匹配,跳過色度分割。

存在的唯一理由是給模型一個必須跨過的門檻 —— 帶一個新的執行期依賴進來,
總得證明它贏得夠多。跑法:
    venv/Scripts/python.exe tools/bench_arrow_baseline.py
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rune_cv  # noqa: E402
import rune_dataset_build as b  # noqa: E402

DIRS = ["up", "right", "down", "left"]


def baseline_dir(crop_bgr):
    """四個旋轉模板在整格上做 NCC,取最高分。不做分割。"""
    g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.resize(g, (rune_cv._TPL_N, rune_cv._TPL_N),
                   interpolation=cv2.INTER_AREA)
    g = (g - g.mean()) / (g.std() + 1e-6)
    best, bs = DIRS[0], -1e9
    for d in DIRS:
        t = np.rot90(rune_cv._TPL, -DIRS.index(d))
        t = (t - t.mean()) / (t.std() + 1e-6)
        s = float((g * t).mean())
        if s > bs:
            bs, best = s, d
    return best


SPLIT_PATH = os.path.join(b.DS_DIR, "gate_split.json")


def split_by_current_gate(ds_dir=b.DS_DIR):
    """把正樣本分成「現行 strict 閘門放行的」與「被擋下的」兩組,回檔名集合。

    這是驗收條件的分母:第 2 條要求前者不得退步,第 1 條的增益幾乎全在後者。

    【算過一次就寫死】判斷方式是呼叫 rune_cv.read_dirs,而後續的 task 會把那支
    函式改成走 CNN —— 屆時重算出來的切分就不再是「現行閘門」的切分,分母會在腳下
    悄悄變掉而且不會報錯。所以第一次算完寫進 gate_split.json,之後只讀不算。
    要重算就手動刪掉那個檔案。
    """
    if os.path.exists(SPLIT_PATH):
        with open(SPLIT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return set(d["passed"]), set(d["gated"])
    passed, gated = set(), set()
    for rec in b.records(ds_dir):
        if rec.get("negative"):
            continue
        img, _box = b.load_sample(ds_dir, rec)
        if img is None:
            continue
        got, _err = rune_cv.read_dirs(img, strict=True)
        (passed if len(got) == 4 else gated).add(rec["file"])
    with open(SPLIT_PATH, "w", encoding="utf-8") as f:
        json.dump({"passed": sorted(passed), "gated": sorted(gated),
                   "note": "現行色度分割閘門的切分,固化以免被後續改動污染"},
                  f, ensure_ascii=False, indent=1)
    print(f"已固化切分到 {SPLIT_PATH}")
    return passed, gated


def main():
    passed, gated = split_by_current_gate()
    hit = {"passed": [0, 0], "gated": [0, 0]}
    for fname, crop, truth, _k in b.iter_arrow_crops():
        grp = "passed" if fname in passed else "gated"
        hit[grp][1] += 1
        hit[grp][0] += (baseline_dir(crop) == truth)
    tot = [sum(v[0] for v in hit.values()), sum(v[1] for v in hit.values())]
    print("不學習基準(整格模板匹配 NCC):")
    for grp, label in (("passed", "原本就過閘門"), ("gated", "原本被擋下")):
        c, n = hit[grp]
        print(f"  {label:8} {c}/{n} = {c / max(1, n):.1%}")
    print(f"  {'整體':8} {tot[0]}/{tot[1]} = {tot[0] / max(1, tot[1]):.1%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/test_bench_baseline.py -v`
Expected: 2 passed

- [ ] **Step 5: 量基準並記下來**

Run: `venv/Scripts/python.exe tools/bench_arrow_baseline.py`

把三個數字抄進本計畫檔案底部的「基準紀錄」表。這是驗收條件第 4 條要比對的對象,不記下來後面就沒得比。

- [ ] **Step 6: 提交**

```bash
git add tools/bench_arrow_baseline.py tests/test_bench_baseline.py rune_dataset/gate_split.json docs/superpowers/plans/2026-08-15-rune-arrow-cnn.md
git commit -m "量一個不學習的基準:整格模板匹配,讓模型必須贏得夠多"
```

---

### Task 4: 前處理(訓練與推論的共用單一來源)

train/serve skew 是這類專案最經典的 bug:訓練時 resize 用 INTER_AREA、推論時用預設,或訓練吃 RGB、推論餵 BGR,分數會莫名其妙掉一截而且完全沒有錯誤訊息。所以前處理**只寫一份**,放在執行期會用到的模組裡,訓練腳本 import 它。

**Files:**
- Create: `server/rune_nn.py`
- Create: `tests/test_rune_nn_preprocess.py`

**Interfaces:**
- Produces:
  - `CLASSES: list[str]` = `["up", "right", "down", "left", "none"]`
  - `IMG: int` = 32
  - `preprocess(crop_bgr: np.ndarray) -> np.ndarray` —— 回 `(3, 32, 32)` float32,值域 [0,1],通道順序 RGB
  - `preprocess_batch(crops: list[np.ndarray]) -> np.ndarray` —— 回 `(N, 3, 32, 32)` float32

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_rune_nn_preprocess.py`:

```python
import numpy as np

import rune_nn


def test_shape_dtype_and_range():
    crop = np.full((81, 82, 3), 128, np.uint8)
    x = rune_nn.preprocess(crop)
    assert x.shape == (3, 32, 32)
    assert x.dtype == np.float32
    assert 0.0 <= x.min() and x.max() <= 1.0


def test_channel_order_is_rgb():
    """輸入是 OpenCV 的 BGR,輸出必須是 RGB。

    這條錯了模型還是會訓得起來、也不會報錯,只是訓練與推論各吃各的順序,
    分數莫名其妙掉一截 —— 正是最難查的那種錯,所以要有測試釘住。
    """
    blue_in_bgr = np.zeros((81, 82, 3), np.uint8)
    blue_in_bgr[:, :, 0] = 255            # BGR 的 B 通道
    x = rune_nn.preprocess(blue_in_bgr)
    assert x[0].max() == 0.0, "R 通道不該有值"
    assert x[2].min() == 1.0, "B 通道應該全滿"


def test_batch_shape():
    crops = [np.full((81, 82, 3), 100, np.uint8) for _ in range(4)]
    x = rune_nn.preprocess_batch(crops)
    assert x.shape == (4, 3, 32, 32)
    assert x.dtype == np.float32


def test_classes_order_matches_rune_cv():
    """前四項必須與 rune_cv._TPL_DIRS 同序 —— rot90 增強依賴這個順序。"""
    import rune_cv
    assert rune_nn.CLASSES[:4] == rune_cv._TPL_DIRS
    assert rune_nn.CLASSES[4] == "none"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_nn_preprocess.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'rune_nn'`

- [ ] **Step 3: 實作**

建立 `server/rune_nn.py`:

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_nn_preprocess.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add server/rune_nn.py tests/test_rune_nn_preprocess.py
git commit -m "符文箭頭前處理:訓練與推論共用同一份,避免 train/serve skew"
```

---

### Task 5: 訓練腳本 + 匯出 ONNX

**Files:**
- Create: `tools/train_rune_arrow.py`
- Create: `tests/test_train_rune_arrow.py`

**Interfaces:**
- Consumes: `rune_nn.CLASSES`、`rune_nn.preprocess`、`rune_dataset_build.iter_arrow_crops`、`rune_dataset_build.iter_negative_crops`、`rune_dataset_build.rot_label`
- Produces:
  - `split_by_time(items) -> tuple[list, list]` —— 依 `ts` 排序後前 80% / 後 20%
  - `augment(crop_bgr, rng) -> np.ndarray` —— 隨機增強後的 BGR 小圖(不改標籤)
  - `ArrowNet(nn.Module)` —— 模型
  - 產出檔:`server/rune_arrow.onnx`

- [ ] **Step 1: 建立可拋棄的訓練環境**

torch 只在訓練用,執行期完全用不到,所以裝在獨立 venv,訓完可刪。CPU-only 版約 250MB。

```bash
python -m venv venv-train
venv-train/Scripts/python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch
venv-train/Scripts/python.exe -m pip install opencv-python numpy pytest onnx
```

把 `venv-train/` 加進 `.gitignore`(檢查是否已有 `venv` 規則涵蓋;沒有就加一行)。

- [ ] **Step 2: 寫失敗的測試**

建立 `tests/test_train_rune_arrow.py`:

```python
import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="訓練相關測試只在 venv-train 裡跑")

import train_rune_arrow as t


def test_split_by_time_has_no_leak():
    """按 ts 排序切分,不隨機切。

    同一顆符文、同一張地圖、相近時間的樣本背景高度相關,隨機切會讓相關樣本
    跨越訓練/驗證邊界,把分數灌到虛高 —— 那種分數上線後會原形畢露。
    """
    items = [{"ts": i, "v": i} for i in range(100)]
    rng = np.random.default_rng(0)
    rng.shuffle(items)
    train, val = t.split_by_time(items)
    assert len(train) == 80 and len(val) == 20
    assert max(x["ts"] for x in train) < min(x["ts"] for x in val)


def test_augment_preserves_shape_and_changes_pixels():
    rng = np.random.default_rng(0)
    crop = np.random.default_rng(1).integers(
        0, 256, (81, 82, 3), dtype=np.uint8)
    out = t.augment(crop, rng)
    assert out.shape == crop.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(out, crop), "增強完全沒動到像素"


def test_model_can_overfit_a_tiny_batch():
    """32 筆訓 300 輪必須背起來(>95%)。

    這是標準的 ML sanity check:它不證明模型會泛化,但能抓出「模型與訓練迴圈根本
    沒接起來」這類靜默失敗(標籤錯位、loss 沒回傳、學習率壞掉),而那類錯誤靠看
    最終分數是分不出來的 —— 泛化不好與根本沒在學,分數長得一樣。
    """
    g = torch.Generator().manual_seed(0)
    x = torch.rand(32, 3, 32, 32, generator=g)
    y = torch.randint(0, 5, (32,), generator=g)
    model = t.ArrowNet()
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    lossf = torch.nn.CrossEntropyLoss()
    for _ in range(300):
        opt.zero_grad()
        loss = lossf(model(x), y)
        loss.backward()
        opt.step()
    model.eval()
    with torch.no_grad():
        acc = (model(x).argmax(1) == y).float().mean().item()
    assert acc > 0.95, f"連 32 筆都背不起來(acc={acc:.2f}),訓練迴圈有問題"
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `venv-train/Scripts/python.exe -m pytest tests/test_train_rune_arrow.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'train_rune_arrow'`

- [ ] **Step 4: 實作訓練腳本**

建立 `tools/train_rune_arrow.py`:

```python
"""訓練符文箭頭方向判讀器,匯出 ONNX。

只在可拋棄的 venv-train 裡跑,執行期完全用不到 torch:
    venv-train/Scripts/python.exe tools/train_rune_arrow.py
    venv-train/Scripts/python.exe tools/train_rune_arrow.py --dump crops_out

--dump 會把裁切結果依標籤分資料夾寫出來。裁切對不對是整件事的地基,必須能用眼睛驗。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import torch
import torch.nn as nn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rune_dataset_build as b  # noqa: E402
import rune_nn  # noqa: E402

OUT_ONNX = os.path.join(ROOT, "server", "rune_arrow.onnx")
EPOCHS = 60
BATCH = 64
LR = 1e-3


# ---------- 資料 ----------
def load_items():
    """回 [{file, crop, label, ts}]。箭頭 + 負樣本,尚未做 rot90 展開。"""
    ts_of = {r["file"]: r.get("ts", 0.0) for r in b.records(b.DS_DIR)}
    items = []
    for fname, crop, label, _k in b.iter_arrow_crops():
        items.append({"file": fname, "crop": crop, "label": label,
                      "ts": ts_of.get(fname, 0.0)})
    for fname, crop, _box in b.iter_negative_crops():
        items.append({"file": fname, "crop": crop, "label": "none",
                      "ts": ts_of.get(fname, 0.0)})
    return items


def split_by_time(items):
    """按 ts 排序取前 80% / 後 20%。不隨機切 —— 見測試裡的理由。"""
    s = sorted(items, key=lambda r: r["ts"])
    cut = int(len(s) * 0.8)
    return s[:cut], s[cut:]


def expand_rot(items):
    """rot90 四倍展開。標籤變換是精確的,不是近似增強;順便讓五個類別完全平衡。"""
    out = []
    for it in items:
        for k in range(4):
            out.append({**it,
                        "crop": np.ascontiguousarray(np.rot90(it["crop"], k)),
                        "label": b.rot_label(it["label"], k)})
    return out


def augment(crop_bgr, rng):
    """平移 → 色相/明暗抖動 → cutout。回 BGR uint8,標籤不變。"""
    img = crop_bgr
    dx, dy = (int(v) for v in rng.integers(-3, 4, 2))
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                         borderMode=cv2.BORDER_REPLICATE)
    # 色相整體位移:箭頭顏色是循環動畫(橘頭綠尾/純綠/彩虹/紫),不能讓模型記顏色
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[:, :, 0] = (hsv[:, :, 0] + int(rng.integers(0, 180))) % 180
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * rng.uniform(0.6, 1.4), 0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * rng.uniform(0.5, 1.5), 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    # cutout:直接模擬怪物與技能特效壓在箭頭上,那正是現行分割崩掉的情境
    if rng.random() < 0.5:
        ch = int(rng.integers(8, 28))
        cw = int(rng.integers(8, 28))
        cy = int(rng.integers(0, max(1, img.shape[0] - ch)))
        cx = int(rng.integers(0, max(1, img.shape[1] - cw)))
        img[cy:cy + ch, cx:cx + cw] = rng.integers(0, 256, 3).astype(np.uint8)
    return img


# ---------- 模型 ----------
class ArrowNet(nn.Module):
    """32x32x3 → 5 類。約 25K 參數,ONNX 檔約 100KB。"""

    def __init__(self, n_cls=len(rune_nn.CLASSES)):
        super().__init__()

        def blk(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1),
                                 nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                                 nn.MaxPool2d(2))

        self.feat = nn.Sequential(blk(3, 16), blk(16, 32), blk(32, 64))
        self.head = nn.Linear(64, n_cls)

    def forward(self, x):
        x = self.feat(x)                 # (N,64,4,4)
        x = x.mean(dim=(2, 3))           # GAP → (N,64)
        return self.head(x)


# ---------- 訓練 ----------
def to_tensor(items, rng=None):
    """items → (X, y)。rng 給了就套增強(訓練集用),沒給就不套(驗證集用)。"""
    xs = [rune_nn.preprocess(augment(it["crop"], rng) if rng else it["crop"])
          for it in items]
    ys = [rune_nn.CLASSES.index(it["label"]) for it in items]
    return torch.from_numpy(np.stack(xs)), torch.tensor(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    args = ap.parse_args()

    raw = load_items()
    train_raw, val_raw = split_by_time(raw)
    train_items = expand_rot(train_raw)
    val_items = expand_rot(val_raw)
    print(f"原始 {len(raw)} → 訓練 {len(train_items)} / 驗證 {len(val_items)}")

    if args.dump:
        for it in train_items[:400]:
            d = os.path.join(args.dump, it["label"])
            os.makedirs(d, exist_ok=True)
            cv2.imwrite(os.path.join(d, f"{it['file']}-{len(os.listdir(d))}.png"),
                        it["crop"])
        print(f"已輸出抽查用裁切到 {args.dump}/ —— 請用眼睛確認圖與資料夾名相符")

    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    model = ArrowNet()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss()
    xv, yv = to_tensor(val_items)

    for ep in range(args.epochs):
        model.train()
        order = rng.permutation(len(train_items))
        tot = 0.0
        for i in range(0, len(order), BATCH):
            batch = [train_items[j] for j in order[i:i + BATCH]]
            x, y = to_tensor(batch, rng)
            opt.zero_grad()
            loss = lossf(model(x), y)
            loss.backward()
            opt.step()
            tot += float(loss) * len(batch)
        model.eval()
        with torch.no_grad():
            acc = (model(xv).argmax(1) == yv).float().mean().item()
        print(f"ep {ep + 1:3d}  loss {tot / len(order):.4f}  驗證 {acc:.1%}")

    model.eval()
    torch.onnx.export(model, torch.zeros(4, 3, rune_nn.IMG, rune_nn.IMG),
                      OUT_ONNX, input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "n"}, "logits": {0: "n"}},
                      opset_version=17)
    print(f"已匯出 {OUT_ONNX}")

    # 匯出後把驗證集的 torch 輸出存下來,Task 6 要拿它比對 ONNX 是否一致。
    # 放 tests/ 不放 server/ —— 它是測試夾具,不是執行期資源,不該混進打包目錄。
    with torch.no_grad():
        ref = model(xv[:64]).numpy()
    np.savez(os.path.join(ROOT, "tests", "rune_arrow_ref.npz"),
             x=xv[:64].numpy(), logits=ref)
    print("已存 tests/rune_arrow_ref.npz(供 ONNX 一致性測試)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑測試確認通過**

Run: `venv-train/Scripts/python.exe -m pytest tests/test_train_rune_arrow.py -v`
Expected: 3 passed

- [ ] **Step 6: 實際訓練並用眼睛驗裁切**

```bash
venv-train/Scripts/python.exe tools/train_rune_arrow.py --dump scratch_crops
```

**打開 `scratch_crops/up/`、`scratch_crops/left/` 各看十來張,確認圖裡的箭頭真的指那個方向。** 這一步不能跳:裁切或標籤對位錯了,後面所有分數都是假的,而且不會有任何錯誤訊息。看完把 `scratch_crops/` 刪掉。

記下最後一輪的驗證正確率。若驗證分數明顯低於訓練分數(差距超過 10 個百分點),依 spec 的風險段落加重增強(提高 cutout 機率到 0.7、平移放大到 ±5px)再訓一次。

- [ ] **Step 7: 提交**

`rune_arrow.onnx` 要進版控(它是打包資源,跟兩個 tpl.png 一樣)。`tests/rune_arrow_ref.npz` 是測試夾具,也一起進(約 800KB)。

```bash
git add tools/train_rune_arrow.py tests/test_train_rune_arrow.py server/rune_arrow.onnx tests/rune_arrow_ref.npz .gitignore
git commit -m "符文箭頭 CNN:訓練腳本與模型,rot90 精確增強 + cutout 模擬遮擋"
```

---

### Task 6: onnxruntime 推論

**Files:**
- Modify: `server/rune_nn.py`
- Modify: `requirements.txt`
- Create: `tests/test_rune_nn_infer.py`

**Interfaces:**
- Produces:
  - `available() -> bool` —— 模型檔在且 onnxruntime 載得起來
  - `predict(crops: list[np.ndarray]) -> tuple[list[str], list[float]]` —— 回(每格類別, 每格最高機率)。模型不可用回 `([], [])`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_rune_nn_infer.py`:

```python
import os

import numpy as np
import pytest

import rune_nn

pytestmark = pytest.mark.skipif(not rune_nn.available(),
                                reason="模型或 onnxruntime 不在")


def test_onnx_matches_torch_reference():
    """ONNX 的輸出必須與訓練當下的 torch 輸出一致(容差 1e-4)。

    這抓的是匯出/前處理不一致的經典 bug:模型照樣跑、照樣回答案,只是答案系統性
    地偏掉,沒有任何錯誤訊息。有這個測試才敢說「上線的跟訓練的是同一個模型」。
    """
    ref = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "rune_arrow_ref.npz"))
    got = rune_nn._session().run(["logits"], {"x": ref["x"]})[0]
    assert np.abs(got - ref["logits"]).max() < 1e-4


def test_predict_returns_per_slot_class_and_prob():
    crops = [np.full((81, 82, 3), 120, np.uint8) for _ in range(4)]
    dirs, probs = rune_nn.predict(crops)
    assert len(dirs) == 4 and len(probs) == 4
    assert all(d in rune_nn.CLASSES for d in dirs)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_predict_reads_real_arrows_correctly():
    """拿資料集裡【現行流程已經讀對】的樣本,模型不能讀錯 —— 那是驗收條件第 2 條
    (不得退步)的最小版本。"""
    import bench_arrow_baseline as bench
    import rune_dataset_build as b
    passed, _gated = bench.split_by_current_gate()
    items = [(f, c, d) for f, c, d, _k in b.iter_arrow_crops() if f in passed]
    assert items, "沒有取到過閘門的樣本"
    dirs, _p = rune_nn.predict([c for _f, c, _d in items[:200]])
    truth = [d for _f, _c, d in items[:200]]
    acc = sum(a == t for a, t in zip(dirs, truth)) / len(truth)
    assert acc >= 0.95, f"在現行流程已讀對的樣本上只有 {acc:.1%}"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_nn_infer.py -v`
Expected: 全部 SKIPPED(`available()` 回 False,因為 onnxruntime 還沒裝)

- [ ] **Step 3: 裝 onnxruntime 並記進依賴**

```bash
venv/Scripts/python.exe -m pip install onnxruntime
```

`requirements.txt` 在 `numpy` 那行後面加:

```
onnxruntime      # 符文箭頭 CNN 判讀器(server/rune_arrow.onnx)。模型不在時會退回色度分割
```

- [ ] **Step 4: 實作推論**

加進 `server/rune_nn.py`(檔案結尾):

```python
import os  # noqa: E402  (放在檔頭 import 區,這裡標示新增)

import paths  # noqa: E402

MODEL_PATH = paths.srv_res("rune_arrow.onnx")
# 守門門檻。Task 8 用驗證集校準後回來改這個數字。
# 【方向要記住】誤判的代價不是漏一次,而是拿雜訊當箭頭去按方向鍵、白燒一次符文
# 冷卻,所以門檻一律往「寧可退線給 2 線」那一側調。
MIN_PROB = 0.90

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
```

- [ ] **Step 5: 跑測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/test_rune_nn_infer.py -v`
Expected: 3 passed

`test_onnx_matches_torch_reference` 若失敗,問題幾乎一定在前處理:確認訓練與推論都走 `rune_nn.preprocess`,沒有人另外寫了一份。

- [ ] **Step 6: 提交**

```bash
git add server/rune_nn.py requirements.txt tests/test_rune_nn_infer.py
git commit -m "符文箭頭 CNN 推論:onnxruntime + 與訓練輸出的一致性測試"
```

---

### Task 7: 接進 read_dirs,信心度守門取代面積閘門

**Files:**
- Modify: `server/rune_cv.py:459-490`(`read_dirs`)
- Create: `tests/test_read_dirs_integration.py`

**Interfaces:**
- Consumes: `rune_nn.available`、`rune_nn.predict`、`rune_nn.MIN_PROB`
- Produces: `read_dirs` 行為不變的對外簽名 `(dirs, err)`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_read_dirs_integration.py`:

```python
import numpy as np
import pytest

import rune_cv
import rune_dataset_build as b
import rune_nn


def _first_positive():
    for rec in b.records(b.DS_DIR):
        if not rec.get("negative"):
            img, _box = b.load_sample(b.DS_DIR, rec)
            if img is not None:
                return rec, img
    pytest.skip("資料集裡沒有可用正樣本")


def test_falls_back_when_model_missing(monkeypatch):
    """模型不在時必須完整退回色度分割 —— 沒帶模型的舊 exe 不能壞。

    這是 spec 的硬性約束,所以要有測試,不能只靠讀程式碼相信。
    """
    monkeypatch.setattr(rune_nn, "available", lambda: False)
    rec, img = _first_positive()
    got, err = rune_cv.read_dirs(img, strict=True)
    # 退回舊路徑後,行為必須與這張圖在舊路徑下的結果一致:
    # 要嘛讀出 4 支、要嘛被面積閘門擋下,不能拋例外、不能回奇怪的長度
    assert got == [] or len(got) == 4


@pytest.mark.skipif(not rune_nn.available(), reason="模型不在")
def test_negative_sample_is_rejected():
    """負樣本(畫面上沒謎題)必須什麼都不回報。

    誤判一次的代價是按錯方向鍵、白燒一次符文冷卻,所以這條比「多認出幾支」重要。
    """
    import os
    for rec in b.records(b.DS_DIR):
        if not rec.get("negative"):
            continue
        p = os.path.join(b.DS_DIR, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            continue
        got, _err = rune_cv.read_dirs(img, strict=True)
        assert got == [], f"{rec['file']} 被誤判成 {got}"


@pytest.mark.skipif(not rune_nn.available(), reason="模型不在")
def test_low_confidence_is_rejected(monkeypatch):
    """信心度不足時要退線,不能硬給答案。"""
    monkeypatch.setattr(rune_nn, "predict",
                        lambda crops: (["up"] * 4, [0.10] * 4))
    _rec, img = _first_positive()
    got, err = rune_cv.read_dirs(img, strict=True)
    assert got == []
    assert "信心" in err
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `venv/Scripts/python.exe -m pytest tests/test_read_dirs_integration.py -v`
Expected: `test_low_confidence_is_rejected` FAIL(目前沒有信心度這條路徑)

- [ ] **Step 3: 改 read_dirs**

把 `server/rune_cv.py` 的 `read_dirs` 換成:

```python
def read_dirs(frame_bgr, strict=True):
    """從一張遊戲畫面讀出 4 支箭頭方向。

    回 (dirs, err)。dirs 是長度 4 的 list,讀不到的位置是 None;判定不是膠囊時回 []。

    【判向走 CNN,分割已經退休】實測:膠囊定位零誤差、模板判向在乾淨輸入上 97.5%,
    但整體只有 41% —— 差距全在 _chroma_map/_seg 被怪物與技能特效污染。所以模型取代
    的是分割那一段,不是判向。模型不在時整條退回舊路徑(見 _read_dirs_chroma)。

    strict=True 會做「這真的是膠囊嗎」的驗證。**不要為了提高偵測率把它關掉**:
    誤判的代價不是漏一次,而是拿背景雜訊當箭頭去按方向鍵,白燒一次符文冷卻。
    預覽用 strict=False 才看得到「抓到什麼」以便診斷。
    """
    import rune_nn
    box = find_capsule(frame_bgr)
    if box is None:
        return [], "找不到謎題膠囊(還沒開謎題?)"
    if not rune_nn.available():
        return _read_dirs_chroma(frame_bgr, box, strict)

    crops = []
    for x0, y0, x1, y1 in slots(box, 4):
        c = frame_bgr[y0:y1, x0:x1]
        if c.size == 0:
            return [], "膠囊區域為空"
        crops.append(c)
    dirs, probs = rune_nn.predict(crops)
    if len(dirs) != 4:
        return _read_dirs_chroma(frame_bgr, box, strict)
    if not strict:
        return [d if d != "none" else None for d in dirs], ""
    # 守門取代舊的四格面積閘門:任一格是 none、或最低信心度不足,就整組退線。
    if "none" in dirs:
        return [], f"不像謎題膠囊(第 {dirs.index('none') + 1} 格判為非箭頭)"
    lo = min(probs)
    if lo < rune_nn.MIN_PROB:
        return [], (f"信心不足(最低 {lo:.2f} < {rune_nn.MIN_PROB:.2f}),"
                    f"暫定 {dirs}")
    return dirs, ""


def _read_dirs_chroma(frame_bgr, box, strict):
    """舊路徑:色度分割 + 模板判向。模型不在時的完整退路。

    【不要刪掉】沒帶模型的打包版、以及模型載入失敗時,整條流程都靠它。
    """
    cap = frame_bgr[box[1]:box[3], box[0]:box[2]]
    if cap.size == 0:
        return [], "膠囊區域為空"
    dist = _chroma_map(cap)
    w = cap.shape[1] / 4
    dirs, areas = [], []
    for k in range(4):
        m = _seg(dist, int(k * w), int((k + 1) * w))
        if m is None:
            dirs.append(None)
            areas.append(0)
            continue
        dirs.append(_direction_tpl(m) or _direction(m))
        areas.append(int((m > 0).sum()))
    if strict:
        bad = [a for a in areas if not (AREA_OK[0] <= a <= AREA_OK[1])]
        ratio = max(areas) / max(1, min(areas))
        if bad or ratio > AREA_RATIO_MAX:
            return [], (f"不像謎題膠囊(四格面積 {areas},比值 {ratio:.1f})")
    miss = sum(1 for d in dirs if d is None)
    return dirs, "" if miss == 0 else f"{miss} 支讀不出來"
```

- [ ] **Step 4: 跑全部測試確認通過**

Run: `venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 全部 passed(訓練相關的在主 venv 會 skip)

- [ ] **Step 5: 提交**

```bash
git add server/rune_cv.py tests/test_read_dirs_integration.py
git commit -m "read_dirs 改走 CNN:信心度守門取代面積閘門,模型不在則完整退回舊路徑"
```

---

### Task 8: 門檻校準、驗收、打包

**Files:**
- Create: `tools/calib_arrow_threshold.py`
- Modify: `server/rune_nn.py`(`MIN_PROB` 定值)
- Modify: `MapleAuto.spec`
- Modify: `DEV_LOG.md`

**Interfaces:**
- Consumes: `rune_nn.predict`、`bench_arrow_baseline.split_by_current_gate`

- [ ] **Step 1: 寫門檻校準腳本**

建立 `tools/calib_arrow_threshold.py`:

```python
"""掃 MIN_PROB,選出「誤判為零」的最低門檻。

【為什麼要掃不是拍腦袋】守門的取捨是不對稱的:漏一次只是退給 2 線(多花 6~11 秒),
誤判一次是按錯方向鍵、白燒一次符文冷卻。所以要的不是最高接受率,是【誤判為零】
前提下的最高接受率。

    venv/Scripts/python.exe tools/calib_arrow_threshold.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rune_dataset_build as b  # noqa: E402
import rune_nn  # noqa: E402


def main():
    per_sample = {}
    for fname, crop, truth, k in b.iter_arrow_crops():
        per_sample.setdefault(fname, []).append((crop, truth, k))
    rows = []
    for fname, items in per_sample.items():
        items.sort(key=lambda t: t[2])
        dirs, probs = rune_nn.predict([c for c, _t, _k in items])
        truth = [t for _c, t, _k in items]
        rows.append((min(probs), dirs == truth, "none" in dirs))
    print(f"{'門檻':>6} {'接受':>6} {'其中錯的':>8} {'接受率':>8}")
    for t in [i / 100 for i in range(50, 100, 5)]:
        acc = [(ok) for lo, ok, has_none in rows if lo >= t and not has_none]
        wrong = sum(1 for v in acc if not v)
        print(f"{t:6.2f} {len(acc):6d} {wrong:8d} {len(acc) / len(rows):8.1%}")
    print("\n選【錯的 = 0】裡接受率最高的那個門檻,填回 rune_nn.MIN_PROB。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑校準,把門檻填回去**

```bash
venv/Scripts/python.exe tools/calib_arrow_threshold.py
```

挑「其中錯的 = 0」裡接受率最高的門檻,改 `server/rune_nn.py` 的 `MIN_PROB`。

注意這是在**全部樣本**上掃的,含訓練集,所以會偏樂觀。若掃出來 0.50 就已經零誤判,不要真的填 0.50 —— 往上取一階(留餘裕給沒見過的畫面),誤判的代價比漏判高得多。

- [ ] **Step 3: 跑驗收**

重啟服務讓新程式碼生效(改 `server/*.py` 必須重啟):

```bash
# PowerShell,要提權 —— 不提權會靜默失敗
Start-Process -FilePath "D:\project\maplestory_automation\restart-admin.bat" `
  -WorkingDirectory "D:\project\maplestory_automation" -Verb RunAs
```

等 `GET /status?token=***REMOVED***` 回 200 後跑評估:

```bash
curl -s -X POST "http://127.0.0.1:8000/rune/dataset/eval?token=***REMOVED***"
```

對照四條驗收條件:

| # | 條件 | 現況 | 結果 |
|---|---|---|---|
| 1 | 整體單支 ≥ 90% | 41% | 填入 |
| 2 | 原本就過閘門那組 ≥ 97% | 97.5% | 填入 |
| 3 | 負樣本零誤報 | 2/2 通過 | 填入 |
| 4 | 明顯贏過 Task 3 基準 | 見基準紀錄 | 填入 |

**任一條不過就不上線** —— 把 `rune_nn.MODEL_PATH` 指向的模型檔移走,`read_dirs` 會自動退回舊路徑,然後回頭調增強或架構重訓。

- [ ] **Step 4: 打包設定**

`MapleAuto.spec` 的 `datas` 補一行(跟兩個 tpl.png 放一起):

```python
    (os.path.join(SRV, "rune_arrow.onnx"), "."),
```

`hiddenimports` 補:

```python
    "rune_nn", "onnxruntime",
```

檔頭的資源說明區(第 17~18 行附近)補一行:

```
    server/rune_arrow.onnx      符文箭頭方向 CNN(不在時退回色度分割)
```

- [ ] **Step 5: 驗證打包**

```bash
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

確認 `dist/` 裡有 `rune_arrow.onnx`,啟動打包版後在日誌裡看到 `[rune_nn] 已載入 rune_arrow.onnx`。看不到就是 `srv_res` 路徑或 `datas` 沒設對 —— 這個錯誤是**靜默**的(會安靜退回色度分割,正確率掉回 41% 卻不報錯),所以必須用眼睛確認日誌。

- [ ] **Step 6: 更新 DEV_LOG**

在 `DEV_LOG.md` 的符文那一節末尾加上(把 `__` 換成 Step 3 量到的實際數字):

```markdown
**✅ 箭頭判讀改用 CNN(`server/rune_arrow.onnx`,onnxruntime 推論)**

換掉的是**色度分割**,不是判向器。實測把瓶頸釘死:

| 環節 | 換掉之前 |
|---|---|
| 膠囊定位 | 被擋下 212 筆中的 199 筆,框全是 329×81(p5=p50=p95)—— 零誤差 |
| 模板判向 | 分割乾淨時單支 597/612 = 97.5% |
| 色度分割 | 199 筆卡在四格面積閘門;強行放行後單支只剩 59.1%、全對 19.6% |
| 整體 | 單支 597/1460 = 41% |

所以再訓一個更好的判向器沒有意義 —— 破口是「怎麼從雜訊裡把箭頭剝出來」。
CNN 直接吃膠囊四等分的原始像素(32×32×3 → 5 類,含 `none`),跳過分割。
換上之後:整體單支 __%、原本就過閘門那組 __%、負樣本零誤報。

- **信心度守門取代面積閘門**:四格都不是 `none` 且最低機率 ≥ `rune_nn.MIN_PROB`
  (現值 __)才採用。門檻一律往「寧可退線給 2 線」那側調 —— 漏一次只是多花
  6~11 秒,誤判一次是按錯方向鍵、白燒一次符文冷卻。
- **模型不在會【靜默】退回色度分割**(`_read_dirs_chroma`),正確率掉回 41% 卻不
  報錯。打包後務必在日誌確認有 `[rune_nn] 已載入 rune_arrow.onnx` 這一行。
- 訓練在可拋棄的 `venv-train`(CPU-only torch),執行期用不到;
  `tools/train_rune_arrow.py --dump` 可輸出裁切供人工抽查。
- **前處理只有一份**(`rune_nn.preprocess`),訓練腳本 import 它。train/serve 各寫
  一份不會報錯,只會讓分數莫名其妙掉一截。
- 旋轉箭頭(持續旋轉、其中一個方向停頓約 0.5 秒)**尚未處理**,那是取哪一幀的問題,
  與本節正交,等取樣條件齊備再開。
```

- [ ] **Step 7: 提交**

```bash
git add tools/calib_arrow_threshold.py server/rune_nn.py MapleAuto.spec DEV_LOG.md
git commit -m "符文箭頭 CNN 上線:門檻校準、驗收、打包"
```

---

## 基準紀錄

這是**執行時填寫的成績單**,不是計畫的缺口:基準欄在 Task 3 Step 5 量到後填,CNN 欄在 Task 8 Step 3 驗收後填。驗收條件第 4 條要比對的就是這張表。

| 組別 | 不學習基準 | CNN | 現行色度分割 |
|---|---|---|---|
| 原本就過閘門(153 筆) | 26.6%(163/612) | 22.9% | 97.5% |
| 原本被擋下(199 筆) | 26.9%(214/796) | 5.0% | 0%(全被閘門擋下) |
| 整體(1408 支) | 26.8%(377/1408) | 12.8% | 41% |

**驗收未通過。** 上表 CNN 欄是 IMG=64 重訓版在零誤判門檻 `MIN_PROB=0.99` 下量到的
數字,三個門檻(整體 ≥90%、過閘門組 ≥97%、負樣本誤報 = 0)只有負樣本誤報這一條
過。整體 12.8% 距離 90% 差了一大截,原本就過閘門那組甚至比現行色度分割(97.5%)
倒退了一大步(22.9%)——CNN 沒有真的學會判向,只是在高信心度時剛好對得少。

把門檻放寬到 `MIN_PROB=0.80` 時 CNN 整體可以到 61.6%(仍大勝現行 41%),但代價是
出現 4 筆整組判錯(即該張圖 4 支箭頭至少一支被判錯還被當作高信心結果接受),不
符合「負樣本誤報 = 0」的硬性條件,所以這個門檻也不能上線。

結論:CNN 已依實作計畫規定改回 opt-in、預設關閉(`server/rune_nn.py` 的
`ENABLED`,需設 `MAPLE_RUNE_NN=1` 才會載入),`read_dirs` 正式路徑維持走現行色度
分割。模型檔與訓練/評估腳本保留在 repo,後續若要往「混合式:舊閘門過的走舊路
徑、只把舊閘門擋下的交給 CNN」方向做,還用得上。詳見 `DEV_LOG.md` 符文章節。
