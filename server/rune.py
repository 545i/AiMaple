"""符文(紫方塊 rune)自動解除 —— 用常駐 claude CLI 子行程當視覺辨識器。

為什麼是這個架構(前三次嘗試的結論見 DEV_LOG):
  * auto-maple TF 模型缺權重檔,純 CV 判向不穩,本機 VLM 單次 ~7s 太慢。
  * 謎題窗只有 10~14 秒,所以延遲是第一約束。實測 `claude -p`:
      每次重開行程  6.3~7.7s   (光 Node 啟動就 3.4s)
      常駐後續訊息  2.2~4.8s   ← 採用這個
    因此 worker 開起來就不關,並在「走去符文」之前先預熱,把啟動成本藏在走路時間裡。
  * 裁切不會變快(瓶頸是 Read 工具多一次往返),但決定準確度:實測連公告文字一起送
    sonnet 判 0/3,只留箭頭列 3/3。

流程:紫標 → 停巡邏 → 預熱 worker → move_to(紫標側邊) → 按啟動鍵 → 裁圖 → claude 判向
      → 依序按方向鍵 → 驗證紫標消失 → 續巡邏。失敗則交還給「暫停巡邏 + Telegram 通知」。
"""
import json
import os
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time

import cv2
import numpy as np
import paths

# worker 的工作目錄必須是「空目錄」:cwd 指到專案內時 claude 會載入 .claude/、CLAUDE.md、
# git 狀態等專案 context,實測讓單次辨識從 ~3s 膨脹到 >10s。裁切圖也放這裡。
_WORKDIR = os.path.join(tempfile.gettempdir(), "maple_rune")
os.makedirs(_WORKDIR, exist_ok=True)
_SHOT = os.path.join(_WORKDIR, "rune_shot.png")   # 送進 claude 的裁切圖(每次覆寫)
_RAW = os.path.join(_WORKDIR, "rune_raw.png")     # 原始整幀(校準定位參數用)
# 真的按下方向鍵的那一張,連同判讀結果留存。失敗後要查「是判讀錯還是按鍵沒生效」
# 只能靠這張;_SHOT 會被後續重試覆蓋掉。
_PRESSED = os.path.join(_WORKDIR, "rune_pressed.png")
# 自動標註資料集:每次「按下 4 個方向後紫標消失」就等於那組答案被遊戲本身驗證為正確,
# 那張圖 + 那組方向就是一筆完美標註樣本。累積夠多之後可以訓練本地偵測器
# (~10~30ms、離線、無 API 變異),取代或作為 claude 的快速回退。
# 存在專案內而非 %TEMP%:這是有價值的資料,不該被系統清掉。
# 可寫資料 → 素材夾。放 _HERE 的話打包後會落在 PyInstaller 的暫存解壓目錄,
# 自動標註的樣本關掉程式就全部消失(而且不會有任何錯誤訊息)。
_DATASET = paths.data_dir("rune_dataset")
DATASET_MAX = 2000                     # 上限,避免無限長大(舊的不刪,只停止新增)

# ---------- 可調參數(部分已由真符文實測校準) ----------
# 搜尋箭頭的範圍(佔畫面比例),也是自動定位失敗時的固定裁切框。
# 實測:箭頭列是跟著符文/角色走的,不是固定 UI(量到兩次分別在畫面 y 0.26~0.33 與
# 0.31~0.39),所以固定框會切到 → 先用 _locate_arrows 自動定位,失敗才退回這個框。
CROP = (0.22, 0.42, 0.15, 0.85)        # (y0, y1, x0, x1)
                                       # 下緣不能拉太低:畫面 y≈0.44 有傷害數字(MISS 等)
                                       # 同樣高飽和,會被分群挑成「最多成員的那列」而抓錯。
# 定位失敗時的退路框(比搜尋範圍窄)。不要直接把整個搜尋範圍 3 倍放大送出 ——
# 那是 2871x720(≈2756 tokens),實測會吃掉 11 秒逾時、燒光整個謎題窗。
FALLBACK_CROP = (0.22, 0.42, 0.25, 0.75)
FALLBACK_UPSCALE = 2
UPSCALE = 3                            # 裁切後放大倍率:箭頭小,放大顯著提升判讀率
# 箭頭偵測【不看色相,只看飽和度】:實測箭頭顏色會循環動畫(同一顆符文先後出現過
# 橘頭綠尾、以及綠/彩虹橘/紫紅/綠),鎖死色相會漏抓 → 定位失敗。
# 冰原背景是低飽和藍白,只要「高飽和 + 夠亮」就能把箭頭跟背景分開,與顏色無關。
_SAT_MIN, _VAL_MIN = 110, 140
_BLOB_MIN, _BLOB_MAX = 40, 6000        # 單一色塊面積範圍(像素)
_ROW_TOL = 14                          # 同一列的中心 y 容差:4 支箭頭必在同一水平線上,
                                       # 用來甩掉傷害數字、特效等其他高飽和雜訊
_PAD = 24                              # 定位後往外擴的邊界。實測留白不足 → 兩端箭頭被切,
                                       # 被切的那兩支就判錯,中間沒切的都對。

ACTIVATE_KEY = "b"                     # 站在符文上開啟謎題的鍵(已實測確認)
ACTIVATE_WAIT = 0.55                   # 按下後等謎題畫面渲染出來
# 按鍵時序:CV 判向只要幾毫秒,辨識完立刻連按四鍵的節奏不像人在操作。以下區間對齊
# 人類實測值 —— 看懂畫面後的反應/決策約 150~250ms;已知內容的連續輸入約 80~120ms。
# 每次都取區間內的隨機值:固定值本身就不自然,四鍵間隔一模一樣尤其明顯。
# 成本可忽略:最壞情況 0.25 + 4x(0.07+0.12) ≈ 1.0s,謎題窗有 10~14s。
THINK = (0.15, 0.25)                   # 辨識完到按下第一個方向鍵之間的停頓
ARROW_HOLD = (0.04, 0.07)              # 每個方向鍵按住時間
ARROW_GAP = (0.08, 0.12)               # 方向鍵之間間隔
ARRIVE_TIMEOUT = 25.0                  # 走到紫標座標的逾時
# 角色與紫標的可用距離「環」(小地圖 px),由 /rune/calib 實測得出:
#   內圈 4:再近角色就蓋住紫標。注意 dx=0 時謎題照樣開得了,但紫標從小地圖消失,
#          而 _purple_gone() 正是靠「紫標消失」判定解除成功 → 會誤報成功。這是安全問題。
#   外圈 8:再遠按啟動鍵沒反應(8 是邊界:同樣落在 8 的兩次,一次開得了一次沒開)。
# 【已推翻的舊假設】原本記著「落點離散、顆粒度約 4,所以不必精確導航」。那是非精確
# 走位(X_TOL=3)下的觀察;改用 precise 後實測五次落點誤差只有 0~1 格,顆粒度不存在。
# 只要人已經在環內仍然整段跳過 —— 那是省走路時間,與精不精確無關。
#
# 環的上下界都來自 /rune/calib 的實機掃描(紫標 x=14,同層,逐格按啟動鍵):
#   實際距離 2 → 紫標【偵測不到】(purple_visible=false)
#   實際距離 4 → 紫標可見、謎題開得了
#   實際距離 5 → 紫標可見、謎題開得了
# 黃點與紫標在小地圖上都是 6px 寬,中心距 2 等於重疊 4px,剩 2px 過不了偵測的
# 面積/長寬比門檻。而「紫標消失」是判定解除成功的唯一依據 —— 蓋到偵測不到,
# 失敗就會被誤判成成功。所以內圈不是走位精度問題,是【驗證能力的下限】。
RADIUS_MIN, RADIUS_MAX = 3, 6          # 內圈 3:距離 2 實測已看不到紫標,留一格安全邊際。
                                       # 外圈 6:距離 5 實測可開,7~8 實測開不了。
                                       # 【注意】掃描裡的 puzzle_open=false 有一部分是
                                       # 符文冷卻造成的(結果呈 false/true 交替),不能直接
                                       # 當成超距,判上界要以 purple_visible 與多次結果為準。
# 虛擬平台的半寬(見 _push_virtual_platform)。刻意跟 RADIUS_MAX 分開 ——
# 那是「按得到符文」的距離,這是「走得到」的落腳範圍,縮小採集半徑不該連帶縮小可走範圍。
VP_HALF_WIDTH = 10
APPROACH_DX = 5                        # 需要導航時的目標。精確走位有約 -1 格的系統偏差
                                       # (走到容差內就停,tol=1),實測目標 5→落 4、6→落 5,
                                       # 所以取 5 會落在 4~5,正是掃描確認「紫標可見且
                                       # 謎題開得了」的區間。
# 垂直容差:紫標是符文圖示中心、角色點是身體中心,本來就有固定偏差,實測 dy=2 與 dy=4
# 都能正常按開謎題,所以取 4(與 navigator.Y_TOL 一致)。設 2 會把正常位置誤判成「不同平台」。
SAME_LEVEL_DY = 4
# navigator 的 Y_TOL=4:dy 剛好 4 會被判定「已到達」而不會去爬繩,於是 move_to 回報成功
# 但角色原地不動。第一次沒對齊時把目標 y 往上多推這麼多,逼它看成 dy < -Y_TOL 真的上繩。
Y_OVERSHOOT = 5
VERIFY_WAIT = 3.5                      # 按完方向鍵後最多輪詢多久等紫標消失(不是只看一次:
                                       # 紫標不會在按完的瞬間就從小地圖不見)
MAX_ROUNDS = 2                         # 單次嘗試內的「開謎題→辨識」重試次數
RETRY_WAIT = 6.0                       # 同上重試前的等待:符文按失敗後有冷卻,畫面會顯示
                                       # 「暫時收不到效果(剩餘時間:N秒)」,沒等就再按也開不了
MAX_ATTEMPTS = 3                       # 整體重試次數(含重新導航)。必須有:紫標 hook 是邊緣
                                       # 觸發,紫標一直在不會再觸發,不自己重試就等於放棄。
ATTEMPT_GAP = 8.0                      # 整體重試之間的間隔

# ---------- 辨識線路 ----------
# 1 線 純 CV(rune_cv):定位膠囊金色描邊 → 四等分 → 逐支判向。完整幀約 6ms。
#        20 張真實符文圖實測 全對 19/20、單支 79/80。離線、無變異、無額度。
# 2 線 claude CLI:1 線讀不到時才走(沒開謎題、膠囊被擋、畫面尺寸不同…)。
# MAPLE_RUNE_LINE = auto(預設,1→2) | cv(只走 1 線) | claude(只走 2 線)
LINE = os.environ.get("MAPLE_RUNE_LINE", "auto")

# 1 線可以重試:它只花數毫秒,而【每次重試都是新的一幀】—— 箭頭顏色在循環動畫、
# 怪物與技能特效在移動,上一幀被擋住的箭頭下一幀可能就乾淨了。重試 5 次的總成本
# (含抓幀)遠低於 1 秒,相對 10~14 秒的謎題窗可以忽略。
CV_TRIES = 5
CV_GAP = 0.12                          # 每次重試間隔:要換到不同的動畫相位才有意義
# 【怎麼判定 1 線失敗】不是「有沒有回 4 個方向」,而是要【兩幀答案一致】才採用。
# 理由:符文謎題的方向在整個窗內不會變,只有顏色在循環;所以兩幀一致是很強的證據。
# 反過來說,單幀「很有把握但judge錯」的情況(實測 r19 第 4 支)就會因為拿不到一致票
# 而被擋下,交給 2 線 —— 誤判的代價是按錯鍵白燒冷卻,寧可退線也不要賭。
CV_AGREE = 2

# ---------- 2 線後端 ----------
# 曾經試過本地 VLM(Qwen2-VL-2B / Qwen2.5-VL-3B,獨立 venv 常駐子行程),已移除。
# 用 20 張真實符文圖(80 支箭頭、人工判讀真值)實測的結論:
#   * 一次問四支:全對 1/20。提示詞裡的 JSON 範例會被逐字照抄,連沒有箭頭的截圖也照回。
#   * 膠囊定位 + 四等分切圖 + 逐格問(最佳配置):全對 3/20、單支 54/80。
#   * 根因是模型分不出水平鏡像 —— 80 格裡一次都沒輸出過 "left",把圖水平鏡射後
#     88% 的答案原封不動。這是 VLM 的已知弱點,提示詞與放大倍率都改不掉。
# 真實符文約半數箭頭是水平的,所以本地 VLM 在此任務上不可用,不值得留著 5GB 環境。
MODEL = "sonnet"                       # claude 後端用。實測與 haiku 同速(瓶頸在往返不在
                                       # 模型),但 haiku 判真符文 4 支只對 1 支 → 用 sonnet
ASK_TIMEOUT = 13.0                    # 單次辨識逾時。原本 9s,但 CLI 速度會隨 API 負載變動:
                                       # 調參時量到 3.6~5.5s,後來實測變成 5.9~11s,9s 幾乎每輪
                                       # 都逾時 → 每次逾時又殺掉 worker、下輪冷啟更慢,整條流程
                                       # 空轉。放寬到 13s(貼齊謎題窗上緣 10~14s),答案晚到也還
                                       # 有機會有效,至少不會保證失敗。
RESTART_AFTER = 3                      # 問幾次後重開 worker。實測:同一 session 每多一張
                                       # 遊戲截圖,下次就明顯變慢(6s → 11s 逾時),必須壓低。

# 提示詞只講形狀,【不要提顏色】:實測箭頭顏色會循環動畫(橘頭綠尾 / 純綠 / 彩虹 / 紫),
# 早期版本寫死「紅色三角頭指的方向就是答案」,遇到純綠箭頭規則對不上,模型反覆推敲
# 每次都拖到 9 秒逾時。拿掉顏色規則後才穩定。
_PROMPT = (
    "Read the image at {path}. It shows a MapleStory rune puzzle: a horizontal row "
    "of 4 arrow icons inside a rounded translucent bar. "
    "The arrows cycle through colors, so ignore color completely and judge only the "
    "shape: for each arrow, which way does the pointed tip face? "
    "Some arrows may be partly hidden behind terrain, a monster or the character; "
    "read those from whatever part is visible. If an arrow is COMPLETELY covered "
    '(e.g. by a UI window), output "unknown" for it rather than guessing. '
    'Answer immediately with ONLY a JSON object like {{"dirs":["left","up","right","down"]}} '
    "listing the 4 entries from leftmost arrow to rightmost. Do not deliberate. "
    "No other text."
)

_DIR_RE = re.compile(r"\b(up|down|left|right)\b", re.I)


class _Worker:
    """常駐的 claude CLI 子行程(stream-json 雙向),第一次啟動慢、後續快。"""

    def __init__(self):
        self._p = None
        self._q = None
        self._reader = None
        self._asks = 0
        self._lock = threading.Lock()

    def _exe(self):
        return shutil.which("claude.cmd") or shutil.which("claude")

    def _pump(self, proc, q):
        """背景把 stdout 每行丟進 queue,讓 ask() 能設逾時而不被 readline 卡死。"""
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)                      # 行程結束哨兵

    def start(self):
        """確保 worker 活著。回 (ok, msg)。第一次啟動含模型冷啟約 6s。"""
        with self._lock:
            return self._start_locked()

    def _start_locked(self):
        if self._p is not None and self._p.poll() is None:
            return True, "已就緒"
        exe = self._exe()
        if not exe:
            return False, "找不到 claude CLI(不在 PATH)"
        cmd = [exe, "-p",
               "--input-format", "stream-json",
               "--output-format", "stream-json",
               "--verbose",
               "--allowedTools", "Read",
               "--strict-mcp-config",           # 不載入使用者的 MCP servers(純浪費啟動時間)
               "--model", MODEL]
        try:
            self._p = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, cwd=_WORKDIR,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._p = None
            return False, f"啟動失敗: {e!r}"
        self._q = queue.Queue()
        self._asks = 0
        self._reader = threading.Thread(target=self._pump, args=(self._p, self._q),
                                        daemon=True)
        self._reader.start()
        print(f"[rune] claude worker 已啟動({MODEL})")
        return True, "ok"

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """關掉 worker,【連子行程一起】。

        claude 是 .cmd 批次包裝,真正做事的 node 是它的子行程。原本只 p.kill() 只殺得掉
        包裝,node 會留下來變孤兒 —— 實測跑過幾輪解符文後累積 5 個殘留 claude 行程、
        共佔 2.1GB(逾時與 RESTART_AFTER 重開都會產生一個)。Windows 要用 taskkill /T
        才會連整棵行程樹一起收掉。"""
        p, self._p = self._p, None
        if p is None:
            return
        try:
            p.stdin.close()                      # 先好好請它自己結束
        except Exception:
            pass
        try:
            p.wait(timeout=1.5)
            return                               # 乾淨退場,不必動粗
        except Exception:
            pass
        try:                                     # 沒退 → 連整棵行程樹殺掉
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        try:
            p.kill()                             # 保險:taskkill 失敗時至少殺包裝
        except Exception:
            pass
        try:
            p.wait(timeout=2)                    # 收屍,避免留 zombie handle
        except Exception:
            pass

    def alive(self):
        return self._p is not None and self._p.poll() is None

    def warm(self):
        """行程活著【而且已經問過至少一次】。剛 spawn 但沒問過的仍算冷的:
        模型端第一次往返才是貴的那一次(冷 6~11s vs 熱 4~6s)。"""
        return self.alive() and self._asks > 0

    def ask(self, img_path):
        """送圖問方向。回 (dirs, err)。dirs 是 ['up','left',...]。"""
        with self._lock:
            if self._asks >= RESTART_AFTER:      # session 太長會變慢,定期重開
                self._stop_locked()
            ok, msg = self._start_locked()
            if not ok:
                return [], msg
            msg_obj = {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": _PROMPT.format(path=img_path)}]}}
            try:
                self._p.stdin.write(json.dumps(msg_obj) + "\n")
                self._p.stdin.flush()
            except Exception as e:
                self._stop_locked()
                return [], f"送出失敗: {e!r}"
            self._asks += 1

            deadline = time.monotonic() + ASK_TIMEOUT
            text = ""
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    self._stop_locked()          # 逾時的 session 狀態不明,重開
                    return [], f"辨識逾時(>{ASK_TIMEOUT}s)"
                try:
                    line = self._q.get(timeout=left)
                except queue.Empty:
                    self._stop_locked()
                    return [], f"辨識逾時(>{ASK_TIMEOUT}s)"
                if line is None:
                    self._stop_locked()
                    return [], "claude 行程結束"
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "assistant":
                    for b in ev.get("message", {}).get("content", []):
                        if b.get("type") == "text":
                            text += b["text"]
                if ev.get("type") == "result":
                    # CLI 層級的錯誤(認證過期/額度用盡/Read 被拒…)也是 type=result,
                    # 但 is_error=true 且 result 是錯誤訊息。不檢查就會被當成「正常回覆」,
                    # 解析不出方向 → dirs=[] 且 err="" 的沉默失敗(實測 1.2s 就回來,
                    # 正常要 4~6s),外層只看到「辨識失敗」完全不知道真因。
                    if ev.get("is_error"):
                        detail = str(ev.get("result") or ev.get("subtype") or "")[:200]
                        self._stop_locked()      # 錯誤後的 session 狀態不明,重開
                        return [], f"claude CLI 錯誤: {detail}"
                    text = (ev.get("result") or text)
                    break
        dirs = _parse_dirs(text)
        if not dirs:                             # 有回覆但一個方向都抽不出來
            return [], f"回覆無法解析: {text.strip()[:120]!r}"
        return dirs, ""


def _parse_dirs(text):
    """從回覆抽方向。先試 JSON,失敗退回抓關鍵字(模型偶爾吐出無引號的 {dirs:[...]})。"""
    if not text:
        return []
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0)).get("dirs")
            if isinstance(d, list):
                out = [str(x).lower() for x in d
                       if str(x).lower() in ("up", "down", "left", "right")]
                if out:
                    return out[:4]
        except Exception:
            pass
    return [w.lower() for w in _DIR_RE.findall(text)][:4]


_claude_worker = _Worker()


def _w():
    return _claude_worker

# ---------- 對外狀態 / 掛鉤 ----------
_enabled = False
_navigator = None
_keyboard = None
_focus_fn = None
_resume_fn = None
_busy = threading.Lock()
_last = {"ts": 0.0, "dirs": [], "err": "", "ms": 0, "solved": None,
         "approach": None, "line": ""}
# 失敗原因統計:「成功率低」要能拆解才知道該修哪裡 —— 換模型只治「看錯」,
# 治不了逾時與符文冷卻。ms 分布用來判斷 ASK_TIMEOUT 是否還太緊。
# cv_miss / by_line 用來看 1 線接手了多少、還有多少落到 2 線。
_stats = {"rounds": 0, "solved": 0, "timeout": 0, "no_arrows": 0,
          "wrong": 0, "cli_error": 0, "cv_miss": 0, "ms_list": [],
          "by_line": {}}
_detect_frame = None                   # 最近一次辨識用的整幀,供 save_sample 取用


def _stat(key):
    _stats[key] = _stats.get(key, 0) + 1


def set_hooks(navigator=None, keyboard=None, focus_fn=None, resume_fn=None):
    """由 main 註冊。resume_fn 回 (ok,msg),解完符文用來續巡邏。"""
    global _navigator, _keyboard, _focus_fn, _resume_fn
    if navigator is not None:
        _navigator = navigator
    if keyboard is not None:
        _keyboard = keyboard
    if focus_fn is not None:
        _focus_fn = focus_fn
    if resume_fn is not None:
        _resume_fn = resume_fn


def set_enabled(on):
    global _enabled
    _enabled = bool(on)
    print(f"[rune] 自動解除:{'開' if _enabled else '關'}")
    if _enabled:
        threading.Thread(target=worker_warmup, daemon=True).start()   # 先真預熱


def status():
    return {"enabled": _enabled, "busy": _busy.locked(),
            "worker": "ready" if _w().alive() else "stopped",
            "warm": _w().warm(), "line": LINE,
            "model": MODEL, "radius": [RADIUS_MIN, RADIUS_MAX],
            "approach_dx": APPROACH_DX, "last": dict(_last),
            "stats": _stats_summary()}


def _stats_summary():
    """辨識輪次的成敗拆解。分母是【輪次】不是符文顆數:一顆符文可能重試多輪。"""
    ms = _stats["ms_list"]
    s = {k: v for k, v in _stats.items() if k != "ms_list"}
    if ms:
        srt = sorted(ms)
        s["ms_avg"] = int(sum(ms) / len(ms))
        s["ms_p50"] = srt[len(srt) // 2]
        s["ms_max"] = srt[-1]
        s["ms_over_timeout"] = sum(1 for v in ms if v >= ASK_TIMEOUT * 1000)
    if _stats["rounds"]:
        s["solve_rate"] = round(_stats["solved"] / _stats["rounds"], 3)
    return s


def overall_state():
    """給中控頁用的單一狀態:solving(解符文中) / patrol(巡邏中) / stopped(已停止)。
    前端不必自己拼 nav + rune 兩邊的狀態去猜 —— 解符文期間 navigator 也在跑導航,
    只看 nav 會誤顯示成「巡邏中」。"""
    if _busy.locked():
        return "solving"
    if _navigator is not None and _navigator.status().get("running"):
        return "patrol"
    return "stopped"


_WARM = os.path.join(_WORKDIR, "warm.png")


def _warm_image():
    """合成一張 4 箭頭小圖給預熱用:順便驗證模型判向是否正常(正解 左上右下)。"""
    if os.path.exists(_WARM):
        return _WARM
    img = np.full((120, 480, 3), 30, np.uint8)
    r, cy = 26, 60
    for i, d in enumerate(("left", "up", "right", "down")):
        cx = 70 + i * 115
        pts = {"up": [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)],
               "down": [(cx, cy + r), (cx - r, cy - r), (cx + r, cy - r)],
               "left": [(cx - r, cy), (cx + r, cy - r), (cx + r, cy + r)],
               "right": [(cx + r, cy), (cx - r, cy - r), (cx - r, cy + r)]}[d]
        cv2.fillPoly(img, [np.array(pts, np.int32)], (60, 200, 245))
    cv2.imwrite(_WARM, img)
    return _WARM


def worker_warmup(fresh=True):
    """預熱 2 線(claude)worker:(可選)重開行程 + 跑一次完整往返(不只 spawn)。
    只 spawn 的話第一次實際辨識仍要付 6~10s 冷啟,會直接吃掉整個謎題窗。
    fresh=True 會先關掉舊 session:同一 session 塞過遊戲截圖後會越問越慢,所以每次要解
    符文之前都開新的。這 ~5s 成本發生在「走去符文」的路上,不佔謎題窗。回 (ok,msg)。

    只走 1 線時直接略過 —— 1 線是純 CV,沒有冷啟成本,沒必要為用不到的後端付 5 秒。"""
    if LINE == "cv":
        return True, "只走 1 線(CV),無需預熱"
    t0 = time.perf_counter()
    if fresh:
        _w().stop()
    ok, msg = _w().start()
    if not ok:
        return False, msg
    dirs, err = _w().ask(_warm_image())
    ms = int((time.perf_counter() - t0) * 1000)
    if err:
        return False, f"預熱往返失敗({ms}ms): {err}"
    good = dirs == ["left", "up", "right", "down"]
    return True, (f"已預熱 {ms}ms,合成圖判向{'正確' if good else '異常'} {dirs}")


def shutdown():
    _w().stop()


# ---------- 小地圖讀值 ----------
def _dot_now():
    """角色黃點的小地圖座標。navigator.status()["pos"] 只在導航中才更新,平時是 None,
    所以直接讀 minimap(與 navigator 內部同一來源)。抓不到回 None。"""
    import minimap
    try:
        minimap.detect_once()
        s = minimap.status()
        d = s.get("dot")
        if d and s.get("found") and not s.get("dot_stale"):
            return (d["x"], d["y"])
    except Exception:
        pass
    return None


def _purple_now():
    """當前小地圖上的紫標清單 [{'x':..,'y':..}, ...]。抓不到回 None(未知,不等於沒有)。"""
    import minimap
    try:
        minimap.detect_once()
        return minimap._last.get("purple") or []
    except Exception:
        return None


def _purple_gone():
    """紫標是否已消失(解除成功的驗證)。未知一律當作「沒消失」,寧可重試也不要誤報成功。"""
    marks = _purple_now()
    return marks is not None and len(marks) == 0


# ---------- 取幀 / 定位 / 辨識 ----------
def _locate_arrows(frame):
    """在搜尋範圍內用高飽和橘/綠色塊找出 4 支箭頭的外接框。回 (y0,y1,x0,x1) 或 None。
    只做「定位」不判方向 —— 定位靠顏色很穩,判方向靠顏色才是先前 CV 版失敗的地方。"""
    h, w = frame.shape[:2]
    ry0, ry1, rx0, rx1 = (int(h * CROP[0]), int(h * CROP[1]),
                          int(w * CROP[2]), int(w * CROP[3]))
    roi = frame[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, _SAT_MIN, _VAL_MIN), (179, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        s = stats[i]
        if not (_BLOB_MIN <= s[cv2.CC_STAT_AREA] <= _BLOB_MAX):
            continue
        bx, by = int(s[cv2.CC_STAT_LEFT]), int(s[cv2.CC_STAT_TOP])
        bw, bh = int(s[cv2.CC_STAT_WIDTH]), int(s[cv2.CC_STAT_HEIGHT])
        # 箭頭近似正方(實測 27x28/25x24/29x29/23x23,比值≈1);公告文字「解放符文…方向鍵」
        # 是扁長條(90x23、184x25,比值 3.9~7.4)。不濾掉會把整條文字框進來,圖變大又變慢
        # (實測 4.9s → 8.5s),判讀率也下降。
        if not (0.6 <= bw / max(bh, 1) <= 1.7):
            continue
        boxes.append((bx, by, bw, bh))
    if len(boxes) < 3:                      # 箭頭可能被遮住一支,3 支就夠定位
        return None
    # 4 支箭頭必在同一水平線上 → 依中心 y 分群,取成員最多的那群,甩掉傷害數字等雜訊。
    best = []
    for b in boxes:
        cy = b[1] + b[3] / 2
        row = [o for o in boxes if abs((o[1] + o[3] / 2) - cy) <= _ROW_TOL]
        if len(row) > len(best):
            best = row
    if len(best) < 3:
        return None
    x0 = min(b[0] for b in best)
    x1 = max(b[0] + b[2] for b in best)
    y0 = min(b[1] for b in best)
    y1 = max(b[1] + b[3] for b in best)
    return (max(0, ry0 + y0 - _PAD), min(h, ry0 + y1 + _PAD),
            max(0, rx0 + x0 - _PAD), min(w, rx0 + x1 + _PAD))


def _grab_frame(tries=5, gap=0.18):
    """抓遊戲畫面。回 (frame, 有沒有看到箭頭, err)。

    `_locate_arrows` 只當【時機閘門】:偵測到疑似箭頭就立刻抓,沒偵測到也不放棄,
    輪詢完照樣往下送(它會漏抓真箭頭,不能拿來當否決條件)。"""
    import minimap
    frame = None
    seen = False
    for i in range(max(1, tries)):
        frame = minimap._grab_window()
        if frame is None:
            return None, False, "抓不到遊戲畫面(遊戲沒開?)"
        if _locate_arrows(frame) is not None:
            seen = True                     # 箭頭已渲染出來,不用再等
            break
        if i < tries - 1:
            time.sleep(gap)
    cv2.imwrite(_RAW, frame)                # 留原始幀供日後校準
    _last["arrows_seen"] = seen             # 只是時機參考,不影響是否送圖
    return frame, seen, ""


def _write_crop(frame):
    """2 線(claude)用:裁出固定框存 PNG。回 (path, err)。

    【裁切一律用固定框,不用 CV 定位的框】。曾經讓 `_locate_arrows` 決定裁切範圍,
    結果是純負債:唯一一次定位「成功」,裁出來的框裡一支箭頭都沒有(鎖到角色特效與
    右側兩條綠色血條 —— 同一水平線、同樣高飽和),模型回 0 支、白燒一次符文冷卻。
    (註:1 線的 `rune_cv` 改抓【膠囊金色描邊】而不是箭頭,那才是可靠的錨點。)"""
    h, w = frame.shape[:2]
    y0, y1 = int(h * FALLBACK_CROP[0]), int(h * FALLBACK_CROP[1])
    x0, x1 = int(w * FALLBACK_CROP[2]), int(w * FALLBACK_CROP[3])
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return "", "裁切區域為空"
    if FALLBACK_UPSCALE > 1:
        crop = cv2.resize(crop, None, fx=FALLBACK_UPSCALE, fy=FALLBACK_UPSCALE,
                          interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(_SHOT, crop)
    return _SHOT, ""


def _cv_read(first_frame):
    """1 線:最多讀 CV_TRIES 幀,要【兩幀答案一致】才採用。
    回 (dirs, err, 已試次數, 產生該答案的那一幀)。

    最後那個回傳值不能省:重試會換幀,答案可能來自第 3 幀,但存進資料集的若是第 1 幀,
    就會出現「圖上箭頭被擋住、標籤卻是別幀讀到的」錯標樣本 —— 資料集最該避免的就是這個。

    每次重試都抓新的一幀 —— 箭頭顏色在循環動畫、怪物與技能特效在移動,上一幀被擋住
    的箭頭下一幀可能就乾淨了。全部 5 次(含抓幀)遠低於 1 秒。

    【怎麼判定失敗】分三種,都會記進 _stats 好事後拆解:
      no_capsule  找不到膠囊 —— 多半是謎題還沒渲染出來,重試最有價值
      not_capsule 找到膠囊但四格面積驗證沒過 —— 有東西擋住箭頭
      disagree    讀得到但幾幀之間對不起來 —— 分割不穩,不該賭,交給 2 線
    """
    import minimap
    import rune_cv
    seen = {}                       # dirs(tuple) -> 出現次數
    reasons = []
    frame = first_frame
    for i in range(CV_TRIES):
        try:
            d, err = rune_cv.read_dirs(frame)
        except Exception as e:
            d, err = [], f"CV 例外: {e!r}"
        if len(d) == 4 and all(d):
            key = tuple(d)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= CV_AGREE:
                return list(key), "", i + 1, frame
        else:
            reasons.append("no_capsule" if "找不到" in err else
                           "not_capsule" if "不像" in err else "unread")
        if i < CV_TRIES - 1:
            time.sleep(CV_GAP)
            f2 = minimap._grab_window()
            if f2 is not None:
                frame = f2
    if seen:
        best = max(seen.items(), key=lambda kv: kv[1])
        _stat("cv_disagree")
        return [], (f"1 線 {CV_TRIES} 幀未取得一致答案(最多票 {list(best[0])} "
                    f"×{best[1]})"), CV_TRIES, None
    _stat("cv_" + (reasons[-1] if reasons else "unread"))
    return [], f"1 線讀不到({reasons[-1] if reasons else 'unread'})", CV_TRIES, None


def _detect():
    """抓幀 → 1 線 CV(最多 5 幀、要兩幀一致),失敗才退 2 線 claude。回 (dirs, err, 毫秒)。

    【為什麼現在可以「失敗後銜接」】先前的設計是啟動時擇一,理由是延遲會疊加 ——
    第一個後端逾時 13s 之後再跑第二個,10~14 秒的謎題窗早就關了。現在 1 線是純 CV,
    完整幀約 6ms,疊加成本可以忽略,所以改成 1 線先跑、讀不到才交給 2 線。"""
    global _detect_frame
    t0 = time.perf_counter()
    frame, _seen, err = _grab_frame()
    _detect_frame = frame          # 留給 save_sample:解除成功後才知道這張值不值得存。
                                   # 【不要塞進 _last】—— _last 會被 status() 直接
                                   # 序列化成 JSON,numpy 影格會讓整個端點爆掉。
    if err:
        return [], err, int((time.perf_counter() - t0) * 1000)

    line, cv_err = "", ""
    dirs = []
    if LINE in ("auto", "cv"):
        dirs, cv_err, tried, hit_frame = _cv_read(frame)
        _last["cv_tries"] = tried
        if dirs:
            line = "cv"
            _detect_frame = hit_frame   # 存進資料集的必須是產生這個答案的那一幀
        else:
            _stat("cv_miss")
            if LINE == "cv":
                ms = int((time.perf_counter() - t0) * 1000)
                _last.update({"ts": time.time(), "dirs": [], "err": cv_err,
                              "ms": ms, "line": "cv"})
                return [], cv_err or "1 線讀不到", ms

    if not dirs and LINE in ("auto", "claude"):
        path, werr = _write_crop(frame)
        if werr:
            return [], werr, int((time.perf_counter() - t0) * 1000)
        dirs, err = _w().ask(path)
        line = "claude"
        if cv_err:
            err = f"{err}(1 線先失敗: {cv_err})" if err else err

    ms = int((time.perf_counter() - t0) * 1000)
    _last.update({"ts": time.time(), "dirs": dirs, "err": err, "ms": ms,
                  "line": line})
    _stats["by_line"][line] = _stats["by_line"].get(line, 0) + 1
    return dirs, err, ms


def save_sample(frame, dirs, line, verified="purple_gone"):
    """把一筆【已被遊戲驗證正確】的樣本存進資料集。回存檔路徑或 ""。

    只在「按完 4 個方向後紫標消失」時呼叫 —— 那等於遊戲本身認證了這組答案。
    【不要改成一有答案就存】:2 線的答案不是真值(實測 claude 會回 unknown、也會看錯),
    而且 2 線只在 1 線失敗時才跑,那些正是最容易判錯的困難樣本。用未驗證的答案當標籤
    等於把錯標餵進資料集,之後拿它調 CV 參數會被帶偏。
    """
    if frame is None or len(dirs) != 4:
        return ""
    try:
        os.makedirs(_DATASET, exist_ok=True)
        idx = os.path.join(_DATASET, "index.jsonl")
        n = sum(1 for _ in open(idx, encoding="utf-8")) if os.path.exists(idx) else 0
        if n >= DATASET_MAX:
            return ""
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        png = os.path.join(_DATASET, f"{stamp}.png")
        # 只存【搜尋帶】那一段,不存整幀:膠囊與所有已知干擾源(公告橫幅、平台草皮)都在
        # 這一帶裡,調參需要的資訊一個不少,但檔案剩三分之一。整幀 PNG 約 1.2MB,
        # 存滿 DATASET_MAX 會膨脹到 ~2.3GB,不值得。
        import rune_cv
        by0, by1 = rune_cv.search_band(frame.shape[0])
        crop = frame[by0:by1]
        cv2.imwrite(png, crop)
        try:
            import mapdata
            map_id = mapdata.current_map_id()
        except Exception:
            map_id = None
        rec = {"file": os.path.basename(png), "dirs": dirs, "line": line,
               "verified": verified, "ts": time.time(), "map": map_id,
               "frame": [frame.shape[1], frame.shape[0]], "band_y0": by0}
        try:
            rec["capsule"] = rune_cv.find_capsule(crop)   # 座標相對存下來的裁切圖
        except Exception:
            rec["capsule"] = None
        with open(idx, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[rune] 已存標註樣本 {rec['file']} {dirs} (第 {n + 1} 筆,來自 {line})")
        return png
    except Exception as e:
        print(f"[rune] 存樣本失敗: {e!r}")
        return ""


def dataset_status():
    """資料集現況:幾筆、來源線路分佈、標籤是怎麼驗證的。
    正確率不放這裡 —— 那要重跑 1 線,是 dataset_eval() 的事,別讓一個查狀態的呼叫
    默默去跑整批推論。"""
    idx = os.path.join(_DATASET, "index.jsonl")
    recs = []
    if os.path.exists(idx):
        for line in open(idx, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    by_line, by_verify = {}, {}
    pos = [r for r in recs if not r.get("negative")]
    neg = [r for r in recs if r.get("negative")]
    for r in pos:
        k = r.get("line", "?")
        by_line[k] = by_line.get(k, 0) + 1
        v = r.get("verified", "?")
        by_verify[v] = by_verify.get(v, 0) + 1
    # 正負樣本分開報:混在一起看會以為「空白畫面也被當成樣本」。
    # 負樣本是【畫面上沒有謎題】的幀,過關條件是 1 線什麼都不要回報 —— 它防的是
    # 誤判(拿背景雜訊當箭頭去按方向鍵、白燒符文冷卻),跟正樣本一樣重要,
    # 但不該混進「認出幾支箭頭」的分母。
    return {"n": len(pos), "negatives": len(neg), "dir": _DATASET,
            "max": DATASET_MAX, "by_line": by_line, "by_verified": by_verify,
            "negative_notes": [r.get("note", "") for r in neg]}


def dataset_eval():
    """拿資料集重跑 1 線,量它在【真實失敗案例】上的正確率。
    這才是資料集的用途:2 線救回來的樣本,正是 1 線最該補強的地方。"""
    import rune_cv
    idx = os.path.join(_DATASET, "index.jsonl")
    if not os.path.exists(idx):
        return {"n": 0, "exact": 0, "arrows": 0, "total": 0, "rows": []}
    exact = arrows = total = 0
    neg_n = neg_ok = 0
    rows = []
    for line in open(idx, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        p = os.path.join(_DATASET, r["file"])
        img = rune_cv.imread(p) if os.path.exists(p) else None
        if img is None:
            continue
        got, err = rune_cv.read_dirs(img)
        truth = r["dirs"]
        if r.get("negative"):
            # 負樣本沒有箭頭:過關條件是「什麼都不要回報」。誤判一次的代價是按錯鍵
            # 白燒符文冷卻,所以它跟正樣本一樣重要,但不能混進單支正確率的分母。
            neg_n += 1
            ok = len(got) == 0
            neg_ok += ok
            rows.append({"file": r["file"], "negative": True, "pass": bool(ok),
                         "cv": got, "err": err, "note": r.get("note", "")})
            continue
        hit = sum(1 for a, b in zip(got, truth) if a == b) if len(got) == 4 else 0
        exact += (got == truth)
        arrows += hit
        total += 4
        rows.append({"file": r["file"], "truth": truth, "cv": got,
                     "hit": hit, "err": err, "line": r.get("line"),
                     "verified": r.get("verified")})
    return {"n": len(rows) - neg_n, "exact": exact, "arrows": arrows,
            "total": total, "neg_n": neg_n, "neg_pass": neg_ok, "rows": rows}


def capsule_preview_jpeg(scale=3):
    """1 線的膠囊預覽 JPEG:框出膠囊、畫四等分線、標出每格判到的方向。
    只抓一幀就回,不做時機輪詢(預覽是給人看的,不能每次卡 5 次 x 0.18s)。回 bytes 或 None。"""
    import minimap
    import rune_cv
    frame = minimap._grab_window()
    if frame is None:
        return None
    return rune_cv.preview_jpeg(frame, scale=scale)


def detect_now():
    """乾跑:辨識當前畫面,不碰角色。供中控頁校準裁切框與判讀。"""
    dirs, err, ms = _detect()
    return {"ok": not err and len(dirs) == 4, "dirs": dirs, "n": len(dirs),
            "err": err, "ms": ms, "shot": _SHOT, "line": _last.get("line", "")}


# ---------- 解謎流程 ----------
def _jitter(rng):
    """取區間內的隨機值。傳入單一數值時原樣回傳(舊設定相容)。"""
    if isinstance(rng, (tuple, list)):
        return random.uniform(rng[0], rng[1])
    return rng


def _tap(key, hold=None):
    """按一下。用 try/finally 保證放開 —— 中間若拋例外(睡眠被打斷、序列埠瞬斷)
    鍵會一直按住,遊戲裡就是角色不停走/不停攻擊,外顯就像「整個卡住」。"""
    _keyboard.key_down(key)
    try:
        time.sleep(_jitter(ARROW_HOLD if hold is None else hold))
    finally:
        _keyboard.key_up(key)


def _press_dirs(dirs):
    """依序輸入 4 個方向,節奏對齊人類操作(先停頓思考,再逐鍵間隔)。回實際耗時秒數。

    抽成共用函式是因為主解謎流程與 /rune/test 各有一份按鍵迴圈,分開寫遲早會改到
    一邊漏一邊,兩條路徑的節奏就會不一致。"""
    t0 = time.perf_counter()
    time.sleep(_jitter(THINK))          # 看懂 → 決定要按什麼
    for i, d in enumerate(dirs):
        _tap(d)
        if i < len(dirs) - 1:           # 最後一鍵之後不必再等
            time.sleep(_jitter(ARROW_GAP))
    return time.perf_counter() - t0


def _platform_of(x, y, y_tol=SAME_LEVEL_DY):
    """(x,y) 落在哪一塊已記錄的平台上。回 dict 或 None。
    比對【區間 + 高度】而不是只比高度 —— 實測有地圖同時存在 y=28/28/29 三塊平台,
    只比高度會把隔壁那塊當成同一層。"""
    import mapdata
    try:
        plats = mapdata.platforms(mapdata.current_map_id()) or []
    except Exception:
        return None
    best = None
    for pf in plats:
        if abs(pf["y"] - y) > y_tol:
            continue
        if pf["xA"] - 2 <= x <= pf["xB"] + 2:
            if best is None or abs(pf["y"] - y) < abs(best["y"] - y):
                best = pf
    return best


def _same_platform(pos, px, py):
    """角色與符文是否在【同一塊】平台上。任一邊找不到平台就回 None(未知,不阻擋)。"""
    a = _platform_of(pos[0], pos[1])
    b = _platform_of(px, py)
    if a is None or b is None:
        return None
    return (a["y"], a["xA"], a["xB"]) == (b["y"], b["xA"], b["xB"])


def _push_virtual_platform(px, py):
    """【符文所在處視為有平台】把互動半徑那塊範圍當成一塊虛擬平台餵給 navigator。

    為什麼需要:符文不保證落在已記錄的平台上 —— 實測符文出現在 x=125,而該層平台只記到
    xB=124,差 1 格就落進兩塊平台之間的空隙。pathgraph 會把目標當節點加進圖,但目標不在
    任何平台區間內就【沒有任何邊】→ 直接回 "無路徑",角色原地不動、只是暫停巡邏。
    符文本來就長在可站立的地面上,所以有紫標就等於那裡有平台,不必逐一補記錄。
    虛擬平台會被 build_physics 用二段跳(30px 內)自動接上旁邊的真實平台。

    回原本的 terrain fn(給 _pop_virtual_platform 還原);拿不到就回 None。"""
    base = getattr(_navigator, "_terrain_fn", None)
    if base is None:
        return None
    vp = {"y": int(py), "xA": int(px) - VP_HALF_WIDTH, "xB": int(px) + VP_HALF_WIDTH}

    def fn():
        pts, plats = base()
        return pts, list(plats or []) + [vp]

    _navigator.set_terrain_fn(fn)
    print(f"[rune] 虛擬平台 y={vp['y']} x={vp['xA']}..{vp['xB']}")
    return base


def _pop_virtual_platform(base):
    """還原地形來源。一定要在 finally 裡呼叫,否則虛擬平台會殘留影響之後的巡邏。"""
    if base is not None:
        _navigator.set_terrain_fn(base)


# ---------- 走位記錄 ----------
# 【為什麼需要】原本走位失敗只寫進 _last["err"] —— 記憶體裡的單一格,下一次嘗試就被蓋掉。
# 事後完全無從得知「走了哪一側、落在哪、為什麼判失敗」,而走位正是符文流程裡最需要
# 回溯的部分(站到隔壁平台、落到別層,光看結果分不出是哪一種)。成功也記,才有對照組。
_NAV_LOG = os.path.join(paths.data_dir("logs"), "rune_nav.jsonl")
_NAV_LOG_MAX = 2 * 1024 * 1024
_nav_log_n = 0


def _log_nav(rec):
    """追加一筆走位結果。與 navigator 的 nav_moves.jsonl 同目錄、同樣的大小輪替。"""
    global _nav_log_n
    try:
        out = dict(rec)
        out["t"] = round(time.time(), 1)
        with open(_NAV_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
        _nav_log_n += 1
        if _nav_log_n % 50 == 0 and os.path.getsize(_NAV_LOG) > _NAV_LOG_MAX:
            os.replace(_NAV_LOG, _NAV_LOG + ".1")
    except Exception:
        pass


def _check_spot(px, py):
    """角色【現在站的位置】能不能按到這顆符文。回 (ok, info)。

    導航完與「本來就在環內」兩條路徑共用同一套判定 —— 判斷條件寫兩份遲早會分岔。"""
    pos = _dot_now()
    marks = _purple_now()
    info = {"pos": list(pos) if pos else None, "purple": [px, py],
            "dist": abs(pos[0] - px) if pos else None,
            "dy": abs(pos[1] - py) if pos else None,
            "purple_visible": None if marks is None else bool(marks)}
    if not pos:
        info["reason"] = "no_dot"
        return False, info
    # 高度差夠小還不夠 —— 實測有地圖同時存在 y=28/28/29 三塊平台,只比高度會把隔壁
    # 那塊當成同一層,人站在隔壁平台上按鍵當然沒反應。要比對是不是【同一塊】。
    same = _same_platform(pos, px, py)
    info["same_platform"] = same
    if same is False:
        info["reason"] = "other_platform"
        return False, info
    if info["dy"] > SAME_LEVEL_DY:
        info["reason"] = "wrong_level"
        return False, info
    info["reason"] = ""
    return True, info


def _approach(px, py, side):
    """從 side 側走到符文旁(px + side*APPROACH_DX)。只負責走,判定交給 _check_spot。"""
    tx, ty = int(px + side * APPROACH_DX), int(py)
    arrived = _goto(tx, ty)
    # Y_TOL=4 會把差 4 層當成已到達 → 上面那趟可能原地不動。沒對齊就往上
    # 超推一段逼它真的爬(navigator 內建「上繩過頭 → 下跳修正到目標層」)。
    p = _dot_now()
    if p and abs(p[1] - py) > SAME_LEVEL_DY:
        print(f"[rune] dy={abs(p[1] - py)} 仍不同層,往上超推 {Y_OVERSHOOT} 再試")
        arrived = _goto(tx, ty - Y_OVERSHOOT)
    # 補正走位:落點超出可用環就用實際誤差反推目標再走一次,把落點拉回環內。
    p = _dot_now()
    if p and not (RADIUS_MIN <= abs(p[0] - px) <= RADIUS_MAX):
        err_x = p[0] - tx
        print(f"[rune] 落點離符文 {abs(p[0] - px)} 格(誤差 {err_x}),補正走位")
        _goto(tx - err_x, ty)
    return bool(arrived), [tx, ty]


def _goto(tx, ty, precise=True):
    """導航到 (tx,ty) 並等它跑完。回是否成功啟動並抵達。

    【一律用精確模式】navigator 的預設容差 X_TOL=3 會讓落點在離符文 3~9 格之間浮動,
    而可用環只有 5~7 格(RADIUS_MIN..MAX) —— 誤差範圍比環還寬,落在環外是常態。
    這裡的代價【不是】謎題窗:窗是按下 ACTIVATE_KEY 才開始計時的,走位全程在窗外。
    真正的代價是落在環外的兩種後果:
      太遠 → 按啟動鍵沒反應,整輪白費,還吃掉一次符文冷卻(重試要等 RETRY_WAIT)
      太近 → 角色蓋住紫標,「紫標消失」的驗證失效,會把失敗誤判成解除成功
    兩者都得再跑一次完整導航補救(3~7 秒)。精確模式多花約 1 秒把 x 收斂到 ±1,
    換掉的是這些。

    輪詢 50ms 而非 200ms:單純少掉等待端每次平均 100ms 的空轉,status() 幾乎零成本。"""
    ok, msg = _navigator.move_to(int(tx), int(ty), precise=precise)
    if not ok:
        _last["err"] = f"導航失敗: {msg}"
        return False
    t0 = time.monotonic()
    while time.monotonic() - t0 < ARRIVE_TIMEOUT:
        if not _navigator.status().get("running"):
            break
        time.sleep(0.05)
    return bool(_navigator.status().get("arrived"))


def _attempt(px, py):
    """一次完整嘗試。整段期間都掛著虛擬平台 —— 導航與補正走位都需要它,
    少了它 pathgraph 會對「不在已記錄平台上的符文」直接回無路徑。"""
    base = _push_virtual_platform(px, py)
    try:
        return _attempt_inner(px, py)
    finally:
        _pop_virtual_platform(base)         # 一定要還原,否則影響之後的巡邏


def _attempt_inner(px, py):
    """預熱 →(必要時)導航 → 開謎題 → 辨識 → 按方向 → 驗證。回是否解除成功。
    失敗一律回 False 並把原因寫進 _last["err"],由外層決定要不要重試。"""
    if not _w().warm():                  # 已經熱著就別再跑一次暖身往返(API 慢時要 6~11s)
        worker_warmup()                     # 冷的才預熱:成本藏在下面的走路時間裡
    if _focus_fn:
        _focus_fn()

    cur = _dot_now()
    dist = abs(cur[0] - px) if cur else None
    dy = abs(cur[1] - py) if cur else None
    moved = False
    in_ring = (dist is not None and dy is not None
               and RADIUS_MIN <= dist <= RADIUS_MAX and dy <= SAME_LEVEL_DY)
    attempt = _last.get("attempt")
    ok, info, arrived = False, {}, True
    if in_ring:
        # 已經在環內就直接開謎題,省下整段走路時間。
        ok, info = _check_spot(px, py)
        _log_nav(dict(info, attempt=attempt, side=None, moved=False))
        if not ok:
            # 距離在環內【不代表站得對】:同高度不同塊平台的距離也可能落在環內,
            # 那時按鍵不會有反應,還是得走位過去。
            print(f"[rune] 距離在環內但 {info.get('reason')},仍需走位")
    if not ok:
        moved = True
        # 【左右各試一次】起始側取離角色近的那邊(省一趟走路)。
        # 一定要試另一側 —— side 是用「當前位置在符文哪一邊」算出來的,走完失敗後
        # 位置沒有本質改變,重算還是同一側,外層 MAX_ATTEMPTS 再跑幾次也是同一側,
        # 等於永遠不會嘗試另一邊(實測就是落到隔壁平台後一路重試同一側)。
        first = 1 if (cur and cur[0] > px) else -1
        for s in (first, -first):
            arrived, target = _approach(px, py, s)
            ok, info = _check_spot(px, py)
            info.update({"side": s, "target": target, "arrived": arrived,
                         "attempt": attempt, "moved": True})
            _log_nav(info)
            if ok and arrived:
                break
            print(f"[rune] 從{'右' if s > 0 else '左'}側接近失敗"
                  f"({info.get('reason') or '沒走到'})"
                  f"→ {'改試另一側' if s == first else '兩側都失敗'}")
        if not (ok and arrived):
            _last["approach"] = dict(info, dist_before=dist, dy_before=dy)
            _last["err"] = {
                "other_platform": "兩側都站到隔壁平台(高度相近但不同塊),按鍵不會有反應",
                "wrong_level": "兩側都落在不同層,按鍵不會有反應",
                "no_dot": "抓不到角色黃點",
            }.get(info.get("reason"), "兩側都沒走到符文位置")
            print(f"[rune] {_last['err']}")
            return False

    pos = _dot_now()
    marks = _purple_now()
    final = info.get("dist")
    _last["approach"] = dict(info, moved=moved, dist_before=dist, dy_before=dy)
    print(f"[rune] 到位 {_last['approach']}")
    # 紫標被角色蓋掉 → 先退開再說。此時「紫標消失」不代表解除成功,直接解會誤報;
    # 但也不該整輪放棄(實測落點 dist=4 就會蓋住,而 4 是 RADIUS_MIN,等於卡在邊界)。
    # 往外退到環中間,退完看得見就繼續。
    if marks is not None and not marks:
        away = 1 if (pos and pos[0] >= px) else -1
        back = int(px + away * APPROACH_DX)
        print(f"[rune] 角色蓋住紫標(dist={final}),往外退到 {back} 再試")
        _goto(back, int(py))
        pos = _dot_now()
        marks = _purple_now()
        final = abs(pos[0] - px) if pos else None
        _last["approach"].update({"backed_off": True, "dist": final,
                                  "pos": list(pos) if pos else None,
                                  "purple_visible": None if marks is None else bool(marks)})
        if marks is not None and not marks:
            _last["err"] = "退開後紫標仍被蓋住,放棄以免誤判解除成功"
            print(f"[rune] {_last['err']}")
            return False
    # 【不因為距離就放棄】RADIUS_MAX 這個數字是 /rune/calib 量的,而那次掃描的「開不了」
    # 有一部分其實是符文冷卻造成的,不是真的超距 → 拿它硬性放棄會擋掉本來按得到的情況
    # (實測落點 9 格就被擋下,整輪失敗)。真按不到時 _grab_crop 會在 ~1s 內快速失敗,
    # 代價遠小於直接放棄。所以只警告、照樣試。
    if final is not None and final > RADIUS_MAX:
        print(f"[rune] 警告:離紫標 {final} 格(參考上限 {RADIUS_MAX}),仍嘗試按啟動鍵")

    for rnd in range(MAX_ROUNDS):
        if rnd:
            # 符文有冷卻(畫面顯示「暫時收不到效果」),重試前先等它退。
            time.sleep(RETRY_WAIT)
            # 只確保 worker 活著(逾時會殺掉它),【不再整個重開 + 暖身往返】——
            # 那是過度設計:session 膨脹已由 ask() 內的 RESTART_AFTER 處理,而每輪多跑
            # 一次暖身在 API 變慢時要 6~11s,純粹燒掉時間讓整條流程看起來卡住。
            _w().start()
        if _focus_fn:
            _focus_fn()
        _tap(ACTIVATE_KEY)                  # 開啟謎題 → 10~14 秒窗開始計時
        time.sleep(ACTIVATE_WAIT)
        dirs, err, ms = _detect()
        _stat("rounds")
        if ms:
            _stats["ms_list"] = (_stats["ms_list"] + [ms])[-50:]
        print(f"[rune] 第{rnd + 1}輪 辨識 {ms}ms → {dirs} {err}")
        if len(dirs) != 4:
            _last["err"] = err or f"只認出 {len(dirs)} 支箭頭"
            _stat("timeout" if "逾時" in err else
                  "cli_error" if "CLI 錯誤" in err or "無法解析" in err else "no_arrows")
            continue
        try:    # 留存真的按下去的那張供事後查證(判讀錯 vs 按鍵沒生效)。
                # 直接寫 _detect_frame,不要 copy _SHOT —— _SHOT 只有 2 線跑過才會更新,
                # 1 線答題時它可能是上一輪甚至上次執行留下的舊圖,複製過去等於存了一張
                # 無關的圖還標成「按下去的那張」,對它的用途反而是誤導。
            if _detect_frame is not None:
                cv2.imwrite(_PRESSED, _detect_frame)
                _last["pressed_shot"] = _PRESSED
        except Exception:
            pass
        _last["press_ms"] = int(_press_dirs(dirs) * 1000)
        # 驗證用輪詢而非只看一次:解除後小地圖上的紫標不是立刻消失,單看 1.2s 那一瞬間
        # 可能誤判成失敗,接著又白白吃一次符文冷卻。
        t0 = time.monotonic()
        while time.monotonic() - t0 < VERIFY_WAIT:
            if _purple_gone():
                _last["err"] = ""
                _stat("solved")
                print("[rune] 解除成功")
                # 紫標消失 = 遊戲驗證這組答案正確 → 存成標註樣本。
                # 2 線救回來的樣本特別有價值:那正是 1 線判不出來的困難案例。
                _last["sample"] = save_sample(_detect_frame, dirs,
                                              _last.get("line", ""))
                return True
            time.sleep(0.4)
        _last["err"] = "按完方向後紫標仍在"
        _stat("wrong")                      # 有按下 4 鍵但沒解掉 = 真的看錯
    return False


def _solve_flow(purple, resume=True):
    """外層重試迴圈:失敗就重來,直到成功 / 紫標消失 / 用完 MAX_ATTEMPTS。

    這一層是必要的 —— minimap 的紫標 hook 是【邊緣觸發】(前一幀沒有→這幀有),紫標一直在
    就不會再觸發;而 navigator 巡邏每輪的兜底一看到紫標又會立刻停巡邏。少了這層,一次失敗
    就等於卡死:既不會再嘗試,接回巡邏也會被兜底立刻停掉,角色杵在原地。

    收場方式:成功 → 接回巡邏;試完仍失敗 → `navigator.pause_purple()`,
    回到原本的安全行為(暫停巡邏;Telegram 通知 minimap 早就發過了)。
    resume=False 用於手動測試:解完不自動接回巡邏,角色留在原地。"""
    solved = False
    try:
        _navigator.stop()                       # 先停巡邏,避免走位干擾
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                print(f"[rune] 整體第 {attempt + 1} 次嘗試(先等 {ATTEMPT_GAP}s)")
                time.sleep(ATTEMPT_GAP)
            marks = _purple_now()
            if marks is not None and not marks:  # 紫標不見了(已被解掉或消失)
                print("[rune] 紫標已消失,不用再解")
                solved = True
                break
            px, py = ((marks[0]["x"], marks[0]["y"]) if marks else purple[0])
            _last["attempt"] = attempt + 1
            if _attempt(px, py):
                solved = True
                break
        _last["solved"] = solved
    except Exception as e:
        _last["err"] = f"{e!r}"
        print(f"[rune] 解除流程錯誤: {e!r}")
    finally:
        # 兜底放開所有可能被按住的鍵:流程中途拋例外時,殘留的按鍵會讓角色一直走/一直打
        for _k in ("up", "down", "left", "right", ACTIVATE_KEY):
            try:
                _keyboard.key_up(_k)
            except Exception:
                pass
        try:
            if solved and resume and _resume_fn:
                _resume_fn()                    # 解掉了 → 接回巡邏
            elif not solved:
                print("[rune] 放棄,暫停巡邏等人工處理")
                _navigator.pause_purple()       # 試完仍失敗 → 回到原本的安全行為
        except Exception:
            pass
        try:
            _busy.release()
        except Exception:
            pass


def trigger_solve(purple=None, resume=True, force=False, need_patrol=True):
    """紫標 hook 呼叫。回 True=已接手解符文;False=沒接手(main 沿用暫停巡邏)。
    force=True 供手動測試用,略過總開關與巡邏中檢查。
    need_patrol=False 給「巡邏啟動前先解」用 —— 那時 navigator 還沒 running。"""
    if not force and not _enabled:
        return False
    if _navigator is None or _keyboard is None:
        return False
    # 只在巡邏進行中才自動解符文:沒在掛機時角色可能正被手動操作、或在別的地圖/
    # 選單裡,自動跑去解符文會搶走控制權。解完也是要接回巡邏,本來就該綁在一起。
    if need_patrol and not force and not _navigator.status().get("running"):
        print("[rune] 沒在巡邏,不自動解符文")
        return False
    if not purple:
        return False
    if not _busy.acquire(blocking=False):       # 已在解,別重入
        return True
    _last.update({"solved": None, "err": "", "dirs": [], "approach": None})
    threading.Thread(target=_solve_flow, args=(purple, resume), daemon=True).start()
    return True


def solve_if_present(resume=True):
    """場上已經有符文就主動接手。回是否真的接手了。

    為什麼需要主動查而不是只靠 hook:`minimap` 的紫標 hook 有兩道閘門 ——
      ① 邊緣觸發(前一幀沒有→這一幀有):符文在【開巡邏之前】就已經在場上時不會觸發。
      ② 還被 PURPLE_NOTIFY_COOLDOWN(預設 120s)節流:解符文的能力被 Telegram
         通知的冷卻綁架,冷卻內出現的符文一樣不會觸發。
    兩種情況下巡邏每輪的兜底都會因為看到紫標而立刻停 → 開了巡邏卻原地不動。
    所以巡邏啟動前主動查一次。"""
    if not _enabled or _busy.locked():
        return False
    marks = _purple_now()
    if not marks:
        return False
    print(f"[rune] 巡邏啟動前偵測到符文 {marks[0]},先接手解除")
    # need_patrol=False:這是巡邏啟動路徑,navigator 還沒 running,但使用者剛按下開始巡邏
    return trigger_solve([(m["x"], m["y"]) for m in marks], resume=resume,
                         need_patrol=False)


def solve_now(resume=False):
    """手動觸發完整流程(讀當前紫標)。供測試導航段用;預設解完不接回巡邏。"""
    marks = _purple_now()
    if not marks:
        return {"ok": False, "err": "小地圖上找不到紫標"}
    pts = [(m["x"], m["y"]) for m in marks]
    ok = trigger_solve(pts, resume=resume, force=True)
    return {"ok": ok, "purple": [list(p) for p in pts],
            "msg": "已在背景執行,用 /rune/status 看進度" if ok else "已有解除流程進行中"}


# ---------- 校準工具 ----------
def probe():
    """只讀不動:回報角色點與紫標的小地圖座標、距離、紫標是否仍可見。
    用來確認「站在符文旁時紫標會不會被角色蓋掉」—— 這決定 _purple_gone() 的驗證可不可信。"""
    marks = _purple_now()
    pos = _dot_now()
    out = {"pos": list(pos) if pos else None,
           "purple": [[m["x"], m["y"]] for m in (marks or [])],
           "purple_visible": None if marks is None else bool(marks)}
    if pos and marks:
        m = marks[0]
        out["dx"] = pos[0] - m["x"]
        out["dy"] = pos[1] - m["y"]
        out["dist"] = round((out["dx"] ** 2 + out["dy"] ** 2) ** 0.5, 2)
    return out


def _puzzle_open():
    """謎題是否真的開著:抓一幀看得不得到箭頭色塊。比讀畫面文字可靠。"""
    import minimap
    frame = minimap._grab_window()
    if frame is None:
        return None
    return _locate_arrows(frame) is not None


def calibrate(dxs=(0, 3, 6, 9, 12, 15), cooldown=15.0):
    """量測互動半徑:對每個水平距離 dx,走到紫標旁 dx 格 → 按啟動鍵 → 看謎題有沒有開。
    不按方向鍵,讓謎題自然逾時關閉(cooldown)後再試下一個距離。
    目的:夾出 APPROACH_DX 的上下界 ——
      上界 max_open_dist   :還能開啟謎題的最大距離(再遠按 B 沒反應)
      下界 min_visible_dist:紫標仍看得見的最小距離(再近角色會蓋住紫標 → 驗證誤判)"""
    if _navigator is None or _keyboard is None:
        return {"ok": False, "err": "navigator/keyboard 未掛載"}
    marks = _purple_now()
    if not marks:
        return {"ok": False, "err": "小地圖上找不到紫標(符文不在或已被角色蓋住)"}
    px, py = marks[0]["x"], marks[0]["y"]
    cur = _dot_now()
    side = 1 if (cur and cur[0] > px) else -1      # 從角色所在那側靠近,不走過頭
    rows = []
    for dx in dxs:
        row = {"dx": dx, "side": side}
        # 精確走位:非精確模式落點誤差 ±3,而這裡量的就是「距離 dx 能不能開」——
        # 讓導航誤差混進待測量本身,夾出來的上下界會比真值鬆。real_dist 記的是實際
        # 距離,事後仍可對照,但走準了才不必事後修正。
        ok, msg = _navigator.move_to(int(px + side * dx), int(py), precise=True)
        if not ok:
            row["err"] = f"導航失敗: {msg}"
            rows.append(row)
            continue
        t0 = time.monotonic()
        while (time.monotonic() - t0 < ARRIVE_TIMEOUT
               and _navigator.status().get("running")):
            time.sleep(0.2)
        pos = _dot_now()
        vis = _purple_now()
        row.update({"arrived": bool(_navigator.status().get("arrived")),
                    "pos": list(pos) if pos else None,
                    "real_dist": abs(pos[0] - px) if pos else None,
                    "purple_visible": None if vis is None else bool(vis)})
        if _focus_fn:
            _focus_fn()
        _tap(ACTIVATE_KEY)
        time.sleep(ACTIVATE_WAIT)
        row["puzzle_open"] = _puzzle_open()
        print(f"[rune] 校準 {row}")
        rows.append(row)
        time.sleep(cooldown)                       # 等謎題自然關閉再試下一個距離
    opened = [r for r in rows if r.get("puzzle_open") and r.get("real_dist") is not None]
    safe = [r for r in rows if r.get("purple_visible") and r.get("real_dist") is not None]
    return {"ok": True, "purple": [px, py], "rows": rows,
            "max_open_dist": max((r["real_dist"] for r in opened), default=None),
            "min_visible_dist": min((r["real_dist"] for r in safe), default=None)}


def test_activate(solve=False):
    """測試/校準:角色須自己站在符文上 → 焦點 + 按啟動鍵 → 辨識(存 rune_shot.png)。
    solve=True 才會真的按方向鍵。不必開自動解除總開關。"""
    if _keyboard is None:
        return {"ok": False, "err": "鍵盤未連線"}
    if _focus_fn:
        _focus_fn()
    worker_warmup()                             # 確保熱的:冷啟會吃掉整個謎題窗
    _tap(ACTIVATE_KEY)
    time.sleep(ACTIVATE_WAIT)
    dirs, err, ms = _detect()
    pressed = False
    if solve and len(dirs) == 4:
        _press_dirs(dirs)
        pressed = True
        time.sleep(VERIFY_WAIT)
    return {"ok": not err and len(dirs) == 4, "dirs": dirs, "n": len(dirs),
            "err": err, "ms": ms, "pressed": pressed,
            "purple_gone": _purple_gone() if pressed else None}
