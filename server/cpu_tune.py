# -*- coding: utf-8 -*-
"""降低本行程對遊戲的 CPU 干擾。

【問題】伺服器與 MapleStory 會搶到同一顆核心,遊戲出現卡頓。實測這台機器有 16 個邏輯
處理器,兩個行程的親和性都是 0xFFFF(沒有任何一方被綁核)、優先權都是 Normal ——
所以不是字面上的「共用單核」,而是兩個機制造成的:

  1. OpenCV 預設開滿 16 條執行緒。每次小地圖偵測(cvtColor/inRange/connectedComponents)
     與預覽的 resize/imencode,都會把工作噴到【全部】核心。遊戲的主渲染執行緒不管落在
     哪一核都會被這種突發撞到 —— 這是主因,且 OpenCV 的平行化對我們這種小圖幾乎沒好處
     (影格只有 1368x800,分派 16 條執行緒的成本比計算本身還高)。
  2. Python GIL:我們的 CPU 工作等效單核吞吐,但 OS 會在核心間搬動它,所以每一核都可能
     被碰到,無法靠「它只用一核」來假設不會干擾遊戲。

【做法】只調整【本行程】,絕不去動 MapleStory 的優先權或親和性 ——
那既不必要,也可能被反作弊視為異常行為。
"""
import os

# 環境變數必須在 import numpy/cv2 【之前】設定才有效(那些函式庫在載入時就讀走)。
# 這裡只是保險:實際生效的是 main.py 開頭就 import 本模組。
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "2")

# 0 = 不調整優先權(想比較差異時用 MAPLE_LOW_PRIORITY=0)
LOW_PRIORITY = os.environ.get("MAPLE_LOW_PRIORITY", "1") not in ("0", "false", "False")
CV_THREADS = int(os.environ.get("MAPLE_CV_THREADS", "2"))


def apply():
    """回報套用結果(字串清單),供啟動時印出。"""
    out = []

    try:
        import cv2
        before = cv2.getNumThreads()
        cv2.setNumThreads(max(1, CV_THREADS))
        out.append(f"OpenCV 執行緒 {before} → {cv2.getNumThreads()}")
    except Exception as e:
        out.append(f"OpenCV 執行緒設定失敗: {e!r}")

    if LOW_PRIORITY:
        # BelowNormal:兩者搶同一核時讓遊戲先跑。我們的工作是偵測與 I/O,晚幾毫秒無妨;
        # 遊戲掉幀是使用者直接感受到的。不用 Idle —— 那會讓偵測在遊戲滿載時被餓死。
        try:
            import psutil
            p = psutil.Process()
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            out.append("行程優先權 → BelowNormal")
        except ImportError:
            try:                                   # 沒 psutil 就走 Win32 API
                import ctypes
                from ctypes import wintypes
                BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
                k32 = ctypes.windll.kernel32
                # 必須宣告回傳型別:GetCurrentProcess 回的是偽句柄 (HANDLE)-1,
                # 不宣告的話 ctypes 預設當成 int(32 位元)截斷,傳進 SetPriorityClass
                # 就是錯的句柄 → 一律失敗(實測 GetPriorityClass 回 0)。
                k32.GetCurrentProcess.restype = wintypes.HANDLE
                k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                k32.SetPriorityClass.restype = wintypes.BOOL
                h = k32.GetCurrentProcess()
                if k32.SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS):
                    out.append("行程優先權 → BelowNormal (Win32)")
                else:
                    err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
                    out.append(f"行程優先權設定失敗 (SetPriorityClass, err={err})")
            except Exception as e:
                out.append(f"行程優先權設定失敗: {e!r}")
        except Exception as e:
            out.append(f"行程優先權設定失敗: {e!r}")
    else:
        out.append("行程優先權:不調整 (MAPLE_LOW_PRIORITY=0)")

    return out
