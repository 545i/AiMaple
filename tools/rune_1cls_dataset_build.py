# -*- coding: utf-8 -*-
"""產生【單類別】的偵測訓練集:經典 246 筆 + 新款 297 筆,全部標成 arrow。

【為什麼要重做一份,不沿用 detr_annotations_mixed.json】
那一份是先用 rune_wheel.angle_of() 量角度、再 nearest_cardinal() 吸附成方向,
造成兩個問題:

  1. 【漏標 45%】angle_of 回 None 的箭頭連框一起被丟掉 —— 新款 1188 支只標了
     649 支,每筆平均只剩 2 支。畫面上有箭頭卻沒標,等於明確教偵測器「這裡是
     背景」,實測讓分類信心從 0.97 崩到 0.06,production 的 min_score 全濾掉。
  2. 【方向標籤無意義】新款全是旋轉款,吸附的是「旋轉中的瞬間角度」,不是謎題
     答案。同一個外觀會拿到互相矛盾的類別。而旋轉款的答案來自角速度反轉
     (約 67ms 的晃動),單幀偵測器原理上就看不到。

【為什麼可以直接砍掉方向類別】server/rune_detr.py 的 detect_arrows() 只回框、
不回類別,所有呼叫端(rune_cv / rune.py / rune_viz / rune_collect)的方向都走
漸層判讀或 rune_wheel。DETR 的方向類別【沒有任何下游在用】—— 偵測器只需要
把箭頭輪廓框出來,方向由系統另外判定。

【新款的框哪裡來】rune_collect/*/meta.json 的 boxes,即既有線上模型
detect_arrows() 的輸出(band_y0 全為 0,與 band.png 同座標系)。這是偽標籤,
不是人工真值 —— 但它通過了幾何選擇閘門(同列/間距/尺寸/不重疊),抽樣目視
確認四框都落在箭頭上,而替代方案是 45% 漏標,兩害相權取其輕。

用法:
    venv/Scripts/python.exe -X utf8 tools/rune_1cls_dataset_build.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS_DIR = os.path.join(ROOT, "rune_dataset")
COLLECT_DIR = os.path.join(ROOT, "rune_collect")

SRC_ANN = os.path.join(DS_DIR, "detr_annotations.json")          # 經典 246 筆
SRC_SPLIT = os.path.join(DS_DIR, "detr_split.json")              # 經典的既有切分

VAL_EVERY = 5   # 新款每 5 筆抽 1 筆進 val,與經典約 20% 的比例一致


def main(single_class=False):
    """預設產生【5 類】:靜態的 up/right/down/left + 旋轉的 rot。

    【為什麼靜態要保留方向類別 —— 用模型判向而不是顏色】
    既有的判向(rune_cv._chroma_map/_seg 與 rune_wheel.angle_of)都建立在
    「綠尾→黃→紅頭」這個【寫死的配色假設】上。實測 rune_collect 的 1188 支箭頭,
    有 487 支(41%)讀不出角度,色相直方圖顯示遊戲的箭頭配色會跑遍整個色環
    (藍→綠→洋紅、紫→洋紅、綠→青都有)。配色一偏移,輕則讀不出、重則讀錯 ——
    使用者回報的「靜態判向不到七成」「深藍色系箭頭會判錯」就是這個。
    形狀才是配色不變的量,而形狀正是偵測器擅長的,所以方向改由模型輸出。

    【為什麼旋轉款只給 rot、不給方向】旋轉款的答案是箭頭經過答案方向時那次
    約 67ms 的角速度反轉(晃動),單幀影像【原理上】看不到。硬標瞬間角度會讓
    同一個外觀拿到互相矛盾的類別。所以旋轉款只貢獻輪廓,方向仍由 rune_wheel
    看連續幀判定;模型逐支輸出 rot 反而讓系統知道哪幾支該送去做晃動分析。
    """
    out_tag = "1cls" if single_class else "5cls"
    OUT_ANN = os.path.join(DS_DIR, f"detr_annotations_{out_tag}.json")
    OUT_SPLIT = os.path.join(DS_DIR, f"detr_split_{out_tag}.json")
    with open(SRC_ANN, encoding="utf-8") as f:
        classic = json.load(f)["records"]
    with open(SRC_SPLIT, encoding="utf-8") as f:
        split = json.load(f)

    records, train, val = [], list(split["train"]), list(split["val"])

    # 經典款(全為靜態):框原封不動。5 類模式保留既有方向真值 —— 那是遊戲驗證
    # 或人工判讀出來的,是這個專案唯一可信的方向標籤來源。
    for r in classic:
        records.append({"file": r["file"], "boxes": [
            {"x0": b["x0"], "y0": b["y0"], "x1": b["x1"], "y1": b["y1"],
             "label": "arrow" if single_class else b["label"]}
            for b in r["boxes"]]})

    # 新款(全為旋轉):用 meta 的完整四框,不經過 angle_of。標成 rot,【不給方向】。
    new_label = "arrow" if single_class else "rot"
    n_new = 0
    for d in sorted(x for x in os.listdir(COLLECT_DIR) if x.isdigit()):
        mp = os.path.join(COLLECT_DIR, d, "meta.json")
        bp = os.path.join(COLLECT_DIR, d, "band.png")
        if not (os.path.exists(mp) and os.path.exists(bp)):
            continue
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        if not m.get("boxes") or len(m["boxes"]) != 4:
            continue
        y0off = m.get("band_y0", 0)
        rel = os.path.relpath(bp, DS_DIR).replace("\\", "/")
        records.append({"file": rel, "boxes": [
            {"x0": float(b[0]), "y0": float(b[1]) - y0off,
             "x1": float(b[2]), "y1": float(b[3]) - y0off,
             "label": new_label} for b in m["boxes"]]})
        (val if n_new % VAL_EVERY == 0 else train).append(rel)
        n_new += 1

    with open(OUT_ANN, "w", encoding="utf-8") as f:
        json.dump({"records": records}, f, ensure_ascii=False)
    with open(OUT_SPLIT, "w", encoding="utf-8") as f:
        json.dump({"train": train, "val": val}, f, ensure_ascii=False)

    import collections
    cnt = collections.Counter(b["label"] for r in records for b in r["boxes"])
    n_box = sum(cnt.values())
    print(f"經典 {len(classic)} 筆(靜態) + 新款 {n_new} 筆(旋轉) = {len(records)} 筆,"
          f"共 {n_box} 個框(平均 {n_box / len(records):.2f} 支/筆)")
    print(f"類別分佈:{dict(sorted(cnt.items()))}")
    print(f"切分:train {len(train)} / val {len(val)}")
    print(f"輸出:{OUT_ANN}\n     {OUT_SPLIT}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--single-class", action="store_true",
                    help="全部併成單一類別 arrow(只訓練定位,不訓練方向)")
    main(single_class=ap.parse_args().single_class)
