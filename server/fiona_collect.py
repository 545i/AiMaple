# -*- coding: utf-8 -*-
"""菲歐娜解謎的【觀察模式】採集 + 追蹤狀態機。

--------------------------------------------------------------------------
為什麼是狀態機 + ingest(),而不是一支離線腳本
--------------------------------------------------------------------------
離線驗證(餵影片幀)與實機執行(餵 minimap._grab_window())走【同一條路】。
本專案為了「評估重刻一份簡化推論邏輯」吃過大虧(見 MEMORY:評估一定要走
production 入口,最誇張一次 94.9% vs 1.8%),所以這裡不另外寫評估版。

--------------------------------------------------------------------------
為什麼先只觀察、不點擊
--------------------------------------------------------------------------
目前單輪正確率只有 11/12(91.7%),而且 12 輪的 95% 信賴區間是 [64.6%, 98.5%]
—— 寬到無法判斷能不能用。一場 4 輪錯一輪就受懲罰,不該拿真實遊戲下注。
觀察模式下由人自己玩自己點,系統只在旁邊記「我會選哪個」,再拿計分格對答案。
玩家正常玩多久就累積多少,不必刻意錄影。

--------------------------------------------------------------------------
真值怎麼取
--------------------------------------------------------------------------
計分格是遊戲自己畫的:答對填黃色數字(數字就是正確的槽號)。所以
  - 答對的輪次 → 拿到明確真值
  - 答錯/超時 → 只知道「不是玩家點的那個」,記下來但不當正樣本
計分格會被遊戲的黃色準星特效蓋住而誤讀,所以一律取【連續多幀一致】才採信。

--------------------------------------------------------------------------
一場的流程與狀態
--------------------------------------------------------------------------
    IDLE ──看到視窗──▶ WATCH ──白舞台+箭頭──▶ REVEAL(記下目標初始槽)
      ▲                                          │ 燈暗
      │                                          ▼
      └──視窗消失───────── ANSWER ◀──按鈕變藍── SHUFFLE(累積蘑菇帶)
                            │ 按鈕熄滅 → 讀計分格 → 產出一筆 round 記錄
                            └──────────────────▶ SHUFFLE(下一輪)
"""
import json
import os
import time

import cv2
import numpy as np

import fiona_cv as F

# 一輪最多累積幾幀。實測一輪約 100~130 幀(24fps 下 4~5 秒),取 600 當防呆上限:
# 玩家掛在畫面上不動時不會無限吃記憶體。
MAX_BAND_FRAMES = 600
# 計分格要連續幾次讀到相同內容才採信(擋準星特效造成的瞬間誤讀)
SCORE_STABLE_N = 3

# 展示階段的判定要【遲滯】,不能單一門檻。展示→洗牌的轉場是燈光漸暗,舞台亮度
# 會在門檻附近來回震盪(實測 frame 199~221 之間 bright 在 0.42~0.72 之間跳),
# 單門檻會讓狀態抖動;而每抖一次就重算一次箭頭,轉場中蘑菇已轉背,橘色蝴蝶結
# 會被誤認成箭頭,把正確的初始槽覆寫掉(實測場1 的槽1 被改成槽4)。
REVEAL_ENTER = 0.55
REVEAL_EXIT = 0.42
# 連續這麼多幀處於展示狀態,才算「真的是一場的展示階段」。實測真正的展示階段
# 有 99~144 幀,而轉場抖動只有 3~9 幀,取 24 兩邊都有很大餘裕。
REVEAL_MIN_FRAMES = 24
# 按鈕熄滅後,計分格還要一小段時間才更新(實測按鈕窗口 366~380,計分格 388 才
# 填上,差約 8 幀)。所以不能在按鈕熄滅當下讀,要等它真的變化 —— 等不到就當
# 這一輪答錯/超時(那種情況遊戲本來就不會填格)。
SCORE_SETTLE_MAX = 72

# 視窗沒開時,不要每幀都做完整的多尺度搜尋。第一次定位要掃 ~50 個 scale,在
# 1920x1080 上每個 scale 的全圖比對就要約 100ms —— 每幀都做等於卡死。而視窗
# 出現與否是「秒」的尺度(展示階段就有 4~6 秒),漏個幾幀完全無所謂。
SEARCH_EVERY_MISS = 24
# 定位短暫失敗時,沿用上一次視窗位置的寬限幀數。
# 【為什麼要這麼長】作答窗口期間按鈕列會整排變樣(變藍、滑鼠 hover 高亮、紅色
# 倒數條覆蓋),兩個模板的分數都會掉到 0.59~0.63,而且最佳位置會跑到錯誤的地方
# —— 實測 frame 5418~5439 連續失敗 22 幀。寬限期原本設 20,剛好差一點,那一輪
# 之後的整場狀態就被清掉(場2 第 3、4 輪整段消失)。
# 作答窗口最長 4 秒(24fps 下 96 幀),所以取 120 幀留餘裕。視窗真的被關掉時
# 多等 5 秒才重置完全無所謂 —— 那種情況本來就要等下一場開始。
#
# 【根本解法(尚未做)】首次用按鈕列定位成功後,從當前畫面抽出【該版本的標題列】
# 當動態模板。標題列不受按鈕狀態影響,穩定得多;而動態抽取又避開了「標題列文字
# 隨語言版本改變」的問題。
LOST_GRACE_FRAMES = 120


class FionaCollector:
    """餵幀進來,吐出一筆一筆的 round 記錄。

    ingest(frame) 回傳:
      None                     這一幀沒有產出
      {"event": "predict", ...}  作答窗口剛開始,附上這一輪的預測槽
      {"event": "round", ...}    一輪結束,附上預測、玩家實際選擇、真值
    """

    def __init__(self, out_dir=None, save_bands=True):
        self.out_dir = out_dir
        self.save_bands = save_bands
        self.scale = None
        self._miss = 0
        self.reset()
        self._seq = 0
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    # ---------------------------------------------------------------- 狀態
    def reset(self):
        # 【scale 刻意不清掉】UI 縮放是遊戲客戶端的設定,不會因為視窗關掉就改變。
        # 保留它,下次視窗出現時就能走單一 scale 的快速路徑,不必再掃 50 個。
        self.state = "IDLE"
        self.win = None
        self.centers = None
        self.bands = []
        self.cur_slot = None        # 目標【目前】在哪個槽(每輪用真值校正)
        self.init_slot = None       # 展示階段箭頭指的初始槽
        self.round_idx = 0
        self.pending = None         # 作答窗口期間暫存的預測
        self._score_hist = []
        self._last_score = None
        self._arrow_votes = {}
        self._reveal = False        # 遲滯後的展示階段狀態
        self._reveal_streak = 0
        self._reveal_confirmed = False
        self._settle = 0

    def _new_session(self):
        """確認進入一場新的比賽(展示階段夠長)才呼叫,清掉上一場的殘留。"""
        self.init_slot = None
        self.cur_slot = None
        self.round_idx = 0
        self._arrow_votes = {}
        self.bands = []

    def _stable_score(self, cells):
        """連續 SCORE_STABLE_N 次一致才採信,擋準星特效誤讀。"""
        key = tuple((c[0], c[1]) for c in cells)
        self._score_hist.append(key)
        if len(self._score_hist) > SCORE_STABLE_N:
            self._score_hist.pop(0)
        if (len(self._score_hist) == SCORE_STABLE_N
                and all(k == self._score_hist[0] for k in self._score_hist)):
            self._last_score = cells
            return cells
        return None

    # ---------------------------------------------------------------- 主迴圈
    def ingest(self, frame, ts=None):
        ts = ts if ts is not None else time.time()
        if frame is None:
            self.reset()
            return None

        # 拿上一幀的位置當 hint:全圖比對 48.6ms 只夠 20fps,局部 2.7ms 可到 300fps+。
        # 視窗被拖動時局部會落空,那一幀自動退回全圖搜尋(見 fiona_cv.find_window)。
        if self.win is None and self._miss % SEARCH_EVERY_MISS:
            # 視窗【真的沒開】時才節流。就算 scale 已知,單一 scale 的全圖比對在
            # 1920x1080 上仍要約 100ms,每幀都做一樣拖垮。有 win 之後走 hint
            # 局部路徑(2.7ms)才不需要節流。
            self._miss += 1
            return None
        win, score, scale = F.find_window(frame, hint=self.win, scale=self.scale)
        if win is None:
            self._miss += 1
            # 【短暫定位失敗不能重置狀態機】滑鼠劃過按鈕、答對的金光特效都會讓
            # 按鈕列模板瞬間失配(實測命中率 89%,失敗集中在作答窗口前後幾幀)。
            # 早期版本一失敗就 reset + 節流,等於跳過接下來 23 幀,那一輪的蘑菇帶
            # 就殘缺了 —— 測試影片因此從 11/12 掉到 2/4。所以短時間內沿用上一次
            # 的視窗位置,超過寬限期才真的當作視窗關閉。
            if self.win is not None and self._miss <= LOST_GRACE_FRAMES:
                win, scale = self.win, self.scale
            else:
                if self.state != "IDLE":
                    self.reset()
                return None
        else:
            self._miss = 0
        self.win, self.scale = win, scale
        sc = scale

        cells = F.read_scoreboard(frame, win, sc)
        stable = self._stable_score(cells)
        _rev, bright = F.is_reveal(frame, win, sc)
        lit, _blue = F.buttons_lit(frame, win, sc)

        # ---- 展示階段(遲滯,見 REVEAL_ENTER/EXIT 的說明) ----
        was_reveal = self._reveal
        if bright >= REVEAL_ENTER:
            self._reveal = True
        elif bright <= REVEAL_EXIT:
            self._reveal = False

        if self._reveal:
            self._reveal_streak += 1
            slot, _area = F.find_arrow(frame, win, self.centers, sc)
            if slot:
                self._arrow_votes[slot] = self._arrow_votes.get(slot, 0) + 1
            # 【光靠亮度不夠,必須同時看到箭頭】答對時的金光特效也會把舞台亮度
            # 推過 REVEAL_ENTER 並持續一段時間,只用亮度會把它誤判成「新的一場
            # 展示階段」而清空狀態 —— 實測場2 的第 3、4 輪就是這樣整段掉光。
            # 橘色箭頭只在真正的展示階段出現,是可靠的區分特徵。
            if (not self._reveal_confirmed
                    and self._reveal_streak >= REVEAL_MIN_FRAMES
                    and self._arrow_votes):
                votes = dict(self._arrow_votes)
                self._new_session()
                self._arrow_votes = votes      # 這些票屬於新的一場,不能清掉
                self._reveal_confirmed = True
            self.state = "REVEAL"
            return None

        if was_reveal and self._reveal_confirmed:
            # 展示階段真正結束 → 用多數票定初始槽(單幀會被特效/蝴蝶結干擾)
            if self._arrow_votes:
                self.init_slot = max(self._arrow_votes, key=self._arrow_votes.get)
                self.cur_slot = self.init_slot
            self._arrow_votes = {}
            self._reveal_confirmed = False
            self.bands = []
        self._reveal_streak = 0
        if self.state in ("IDLE", "REVEAL"):
            # 中途才看到視窗(沒看到展示階段)時 cur_slot 仍是 None,
            # 那種情況下只觀察、不預測。
            self.state = "SHUFFLE"

        # 槽位校準要在【洗牌階段】做:展示階段是白舞台,沒有聚光燈峰可找,
        # slot_centers 只會退回等分值。
        if self.centers is None and not lit:
            self.centers = F.slot_centers(frame, win, sc)

        # ---- 作答窗口 ----
        if lit:
            if self.state != "ANSWER":
                self.state = "ANSWER"
                self.pending = self._predict(ts)
                return self.pending
            return None

        if self.state == "ANSWER":
            # 按鈕熄滅只代表玩家點了/超時了,計分格還要幾幀才更新 → 進 SETTLE 等
            self.state = "SETTLE"
            self._settle = 0
            return None

        if self.state == "SETTLE":
            self._settle += 1
            cur = stable or self._last_score
            filled = (cur and self.round_idx < len(cur)
                      and cur[self.round_idx][0] != "empty")
            if filled or self._settle >= SCORE_SETTLE_MAX:
                rec = self._finish_round(cur, ts)
                self.state = "SHUFFLE"
                self.bands = []
                return rec
            return None

        # ---- 洗牌中:累積蘑菇帶 ----
        band = F.crop(frame, win, F.BAND, sc)
        if band is not None and len(self.bands) < MAX_BAND_FRAMES:
            # 【存彩色,不要在這裡轉灰】洗牌時蘑菇是半透明的,灰階下它跟明亮的
            # 聚光燈【亮度幾乎相同】—— 實測:模板比對一律黏在背景上(分數 0.89
            # 卻框在空舞台)、連通塊 82% 的幀糊成一塊、能量圖是一片模糊的斜帶。
            # 但兩者【顏色】完全不同(粉紅蘑菇 vs 黃/綠/紫/粉聚光燈)。
            # 參考資料集(Roboflow 1-gnqic v6,同一個謎題)也是彩色標註的。
            # band_energy 內部仍會轉灰,追蹤行為完全不變;彩色只是【額外】留著。
            self.bands.append(band)
        return None

    # ---------------------------------------------------------------- 預測
    def _predict(self, ts):
        """作答窗口開始的那一刻做預測。

        【非因果但合法】band_energy 的時間中位數與 Viterbi 都需要整段,而這裡
        用的正是「本輪開始到現在」這段 —— 洗牌已經全部發生完了,不需要逐幀
        即時輸出。這是這個問題允許的、也是最該利用的結構。
        """
        out = {"event": "predict", "ts": ts, "round_idx": self.round_idx,
               "from_slot": self.cur_slot, "n_frames": len(self.bands),
               "pred": None, "conf": None}
        if self.cur_slot is None or len(self.bands) < 5:
            return out
        P = F.band_energy(self.bands)
        if P is None:
            return out
        w = P.shape[1]
        cen = self.centers or [(k + 0.5) * w / 4 for k in range(4)]
        # 【轉移上限要跟著 scale 走】MAX_STEP_PX 是在 scale=1(1370 寬)量出來的
        # (24fps 下每幀位移 p99 = 5.4px,取 8 留餘裕)。在 1920 寬的畫面上 UI 放大
        # 1.409 倍,蘑菇寬度從 103px 變成 145px,位移也等比變大 —— 不跟著縮放
        # 等於把約束勒緊了 1.4 倍。
        step = max(2, int(round(F.MAX_STEP_PX * (self.scale or 1.0))))
        path = F.viterbi_track(P, cen[self.cur_slot - 1], max_step=step)
        out["pred"] = F.slot_of(float(path[-1]), cen, w)
        out["conf"] = F.path_confidence(P, path)
        self._pred_path = path
        self._pred_P = P
        return out

    # ---------------------------------------------------------------- 收尾
    def _finish_round(self, cells, ts):
        """一輪結束:比對預測與計分格真值,存檔。

        計分格新填的那一格 = 玩家這一輪的結果。黃色代表答對,那個數字就是正確
        的槽號;沒有新增或變紅代表答錯/超時,此時【拿不到正確答案】,只能記錄。
        """
        pred = (self.pending or {}).get("pred")
        idx = self.round_idx
        truth = None
        correct = None
        if cells and idx < len(cells):
            col, dig = cells[idx]
            if col == "yellow" and dig and dig != "0":
                truth = int(dig)
                correct = (pred == truth) if pred else None

        rec = {"event": "round", "ts": ts, "round_idx": idx,
               "from_slot": self.cur_slot, "init_slot": self.init_slot,
               "pred": pred, "truth": truth, "correct": correct,
               "n_frames": (self.pending or {}).get("n_frames"),
               "conf": (self.pending or {}).get("conf"),
               "win": list(self.win) if self.win else None, "scale": self.scale,
               "score_cells": [[c[0], c[1]] for c in cells] if cells else None}
        self._save(rec)

        # 用真值校正追蹤起點:答對時遊戲等於告訴我們目標就在那個槽,
        # 這能阻止誤差跨輪累積。答錯時無從校正,只能沿用自己的預測。
        if truth:
            self.cur_slot = truth
        elif pred:
            self.cur_slot = pred
        self.round_idx += 1
        self.pending = None
        return rec

    def _save(self, rec):
        if not self.out_dir:
            return
        d = os.path.join(self.out_dir, f"{self._seq:04d}")
        os.makedirs(d, exist_ok=True)
        self._seq += 1
        rec["dir"] = d
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fp:
            json.dump(rec, fp, ensure_ascii=False, indent=1)
        # 【只存原始資料】——帶序列是原始觀測,不存任何模型/演算法的中間判斷當
        # 標籤。這條規矩見 server/rune_collect.py:拿自己的輸出當標籤等於固化錯誤。
        if self.save_bands and self.bands:
            np.savez_compressed(os.path.join(d, "bands.npz"),
                                bands=np.array(self.bands, dtype=np.uint8))

    # ---------------------------------------------------------------- 統計
    @staticmethod
    def summarize(out_dir):
        """掃過採集目錄,回單輪正確率統計(只算拿得到真值的輪次)。"""
        n = ok = 0
        no_truth = 0
        for name in sorted(os.listdir(out_dir)):
            p = os.path.join(out_dir, name, "meta.json")
            if not os.path.exists(p):
                continue
            r = json.load(open(p, encoding="utf-8"))
            if r.get("truth") is None or r.get("pred") is None:
                no_truth += 1
                continue
            n += 1
            ok += int(bool(r.get("correct")))
        return {"rounds_with_truth": n, "correct": ok,
                "accuracy": (ok / n) if n else None, "unusable": no_truth}
