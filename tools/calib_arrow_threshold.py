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
    if not rune_nn.available():
        sys.exit("rune_nn 預設關閉(驗收未過)。要校準門檻請設 MAPLE_RUNE_NN=1 再跑。")

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
