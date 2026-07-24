# -*- coding: utf-8 -*-
"""Arduino HID 鍵盤橋接。

與 arduino_keyboard.ino 韌體協定相容（序列埠, 115200）：
    <token>\n        -> tap（按下+釋放），回 OK/ERR
    DOWN:<token>\n   -> 按住
    UP:<token>\n     -> 釋放

為了遠端遊玩的低延遲，所有寫入都丟進一個佇列由「單一 worker 執行緒」序列化送出，
不阻塞 FastAPI 的事件迴圈，也避免多執行緒交錯破壞 request/response 框架。
"""
import queue
import threading
import time

try:
    import serial
except ImportError:
    serial = None

from config import ARDUINO_PORT, ARDUINO_BAUD, ARDUINO_WAIT_ACK


def _token(key):
    """把前端送來的按鍵名稱轉成韌體看得懂的 token。"""
    k = str(key).lower()
    if k.startswith("num") and k[3:].isdigit():
        return "N" + k[3:]                     # num0 -> N0
    if k.startswith("f") and k[1:].isdigit():
        return k.upper()                        # f1 -> F1
    named = {
        "space": "SPACE", "enter": "ENTER", "return": "ENTER",
        "tab": "TAB", "esc": "ESC", "escape": "ESC",
        "backspace": "BACKSPACE",
    }
    if k in named:
        return named[k]
    # 單一字元 (a-z/0-9) 與方向鍵/導覽鍵/修飾鍵，韌體不分大小寫
    return k


# 解除卡死用的完整按鍵清單(方向/數字/字母/修飾/常用/功能/小鍵盤)。
_ALL_KEYS = (["left", "right", "up", "down"]
             + [str(i) for i in range(10)]
             + list("abcdefghijklmnopqrstuvwxyz")
             + ["shift", "ctrl", "alt", "space", "enter", "tab", "esc",
                "backspace", "home", "end", "pageup", "pagedown", "insert", "delete"]
             + ["f" + str(i) for i in range(1, 13)]
             + ["num" + str(i) for i in range(10)])


class ArduinoKeyboard:
    def __init__(self, port=ARDUINO_PORT, baud=ARDUINO_BAUD):
        self.port = port
        self.baud = baud
        self._ser = None
        self._q = queue.Queue()
        self._worker = None
        self.connected = False
        # 最後觸發的方向鍵 = 角色當前面向(方向預覽用)。任何來源(WS/校準/掛機/
        # 轉向)按下 left/right 都會更新這裡,達成「最後觸發即當前方向」。
        self.last_dir = "right"

    def _detect_port(self):
        """自動找 Arduino 的序列埠(依描述/VID)，避免燒錄後埠號變動。"""
        try:
            import serial.tools.list_ports as lp
        except Exception:
            return None
        for p in lp.comports():
            s = f"{p.description} {p.hwid} {p.manufacturer}".upper()
            if "ARDUINO" in s or "LEONARDO" in s or "VID:PID=2341" in s or "2341:" in s:
                return p.device
        return None

    def start(self):
        if serial is None:
            print("[Arduino] pyserial 未安裝，鍵盤停用")
            return False
        port = self._detect_port() or self.port    # 自動偵測，找不到才用設定值
        try:
            self._ser = serial.Serial(port, self.baud, timeout=0.05)
            self.port = port
            time.sleep(2)  # 等 Arduino 重置
            self.connected = True
            print(f"[Arduino] 已連線 {port}")
        except Exception as e:
            print(f"[Arduino] 連線失敗: {e}")
            self.connected = False
            return False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        return True

    @staticmethod
    def _parse_mmove(cmd):
        try:
            x, y = cmd[6:].split(",", 1)
            return int(x), int(y)
        except Exception:
            return 0, 0

    def _write_cmd(self, cmd):
        if not self.connected or self._ser is None:
            return
        try:
            self._ser.write((cmd + "\n").encode())
            if ARDUINO_WAIT_ACK:
                self._ser.readline()           # 等 OK/ERR（較可靠、較慢）
            elif self._ser.in_waiting:
                self._ser.read(self._ser.in_waiting)   # 射後不理：讀掉緩衝避免累積
        except Exception as e:
            print(f"[Arduino] 送出錯誤: {e}")
            self.connected = False

    def _run(self):
        """單一 worker：從佇列取指令、依序送出。

        會把『連續的』MMOVE 合併成一筆再送：觸控拖曳會產生大量高頻 MMOVE，
        115200 序列埠追不上就會在無界佇列裡越積越多、延遲只增不減。合併後
        等效於用序列埠能負荷的速率送出「累加位移」，位移總量不變(韌體會自動
        切成 ±127 分段)。遇到非 MMOVE(按鍵/滑鼠鍵/滾輪)即停止合併，確保順序不變。"""
        while True:
            cmd = self._q.get()
            if cmd is None:
                break
            if isinstance(cmd, str) and cmd.startswith("MMOVE:"):
                dx, dy = self._parse_mmove(cmd)
                trailing = None      # 合併時撞到的非 MMOVE 指令(合併送完後接著送)
                closing = False
                while True:
                    try:
                        nxt = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        closing = True
                        break
                    if nxt.startswith("MMOVE:"):
                        ndx, ndy = self._parse_mmove(nxt)
                        dx += ndx; dy += ndy
                    else:
                        trailing = nxt
                        break
                self._write_cmd(f"MMOVE:{dx},{dy}")
                if trailing is not None:
                    self._write_cmd(trailing)
                if closing:
                    break
            else:
                self._write_cmd(cmd)

    def tap(self, key):
        if key in ("left", "right"):
            self.last_dir = key
        self._q.put(_token(key))

    # ===== 滑鼠(硬體 HID，需燒錄 arduino_kbm 韌體) =====
    def wheel(self, n):
        self._q.put(f"WHEEL:{int(n)}")            # 滾輪 n 格(正=上/前)

    def mouse_move(self, dx, dy):
        self._q.put(f"MMOVE:{int(dx)},{int(dy)}")

    def mouse_button(self, button, down):
        b = {"left": "L", "right": "R", "middle": "M"}.get(button, "L")
        self._q.put(("MDOWN:" if down else "MUP:") + b)

    def key_down(self, key):
        if key in ("left", "right"):
            self.last_dir = key
        self._q.put("DOWN:" + _token(key))

    def key_up(self, key):
        self._q.put("UP:" + _token(key))

    def release_all(self):
        """對所有已知按鍵送 UP:(解除卡死)。用於程式被強殺後韌體仍按著某鍵、
        一直重複輸出的情況——中控台「解除卡死」按鈕呼叫此方法一次全放開。"""
        for k in _ALL_KEYS:
            self._q.put("UP:" + _token(k))
        # 順手也放開滑鼠三鍵(若燒錄了鍵鼠韌體;未支援則韌體回 ERR,無害)
        for b in ("L", "R", "M"):
            self._q.put("MUP:" + b)

    def close(self):
        try:
            self._q.put(None)
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self.connected = False


class ArduinoMouse:
    """沒有 KMBox(km.dll) 時的滑鼠降級：透過同一顆 Arduino 的 HID 滑鼠送指令。

    仍是硬體 HID 訊號(反作弊偵測不到)，優於 SoftMouse(SendInput 軟體注入)。
    介面與 KMouse/SoftMouse 相容(move_relative / button_* / click …)，讓 main.py
    無縫替換。限制：HID 滑鼠只能相對移動，不支援絕對 move_to；本專案的絕對映射(t=ma)
    在輸入端已轉成相對差值(video_pipeline.abs_delta)，故不受影響。

    與鍵盤共用同一顆 Arduino 的序列埠佇列(單一 worker 序列送出)，不需另開連線。
    """
    software = False   # 硬體 HID，非軟體注入

    def __init__(self, keyboard):
        self._kb = keyboard
        # 累積不足 1 像素的小數位移，避免慢速移動被 int() 截掉而完全不動
        self._rx = 0.0
        self._ry = 0.0

    @property
    def connected(self):
        return getattr(self._kb, "connected", False)

    def start(self):
        print("[ArduinoMouse] 無 KMBox → 降級用 Arduino HID 滑鼠(硬體訊號，反作弊安全)")
        return self.connected

    def _emit(self, dx, dy):
        self._rx += float(dx); self._ry += float(dy)
        ix, iy = int(self._rx), int(self._ry)      # 往零截斷，保留小數餘量
        self._rx -= ix; self._ry -= iy
        if ix or iy:
            self._kb.mouse_move(ix, iy)

    def move_relative(self, dx, dy):
        self._emit(dx, dy)

    def move_relative_smooth(self, dx, dy, delay=8, delta=10):
        self._emit(dx, dy)      # HID 無曲線平滑，直接相對移動

    def move_to(self, x, y):
        pass                    # HID 相對滑鼠無法絕對定位；本專案未經由此路徑

    def button_down(self, button="left"):
        self._kb.mouse_button(button, True)

    def button_up(self, button="left"):
        self._kb.mouse_button(button, False)

    def click(self, button="left", min_delay=0, max_delay=0):
        self._kb.mouse_button(button, True)
        self._kb.mouse_button(button, False)

    def close(self):
        pass                    # 序列埠由 keyboard.close() 統一關閉
