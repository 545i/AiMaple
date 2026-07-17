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


class ArduinoKeyboard:
    def __init__(self, port=ARDUINO_PORT, baud=ARDUINO_BAUD):
        self.port = port
        self.baud = baud
        self._ser = None
        self._q = queue.Queue()
        self._worker = None
        self.connected = False

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

    def _run(self):
        """單一 worker：從佇列取指令、依序送出。"""
        while True:
            cmd = self._q.get()
            if cmd is None:
                break
            if not self.connected or self._ser is None:
                continue
            try:
                self._ser.write((cmd + "\n").encode())
                if ARDUINO_WAIT_ACK:
                    self._ser.readline()       # 等 OK/ERR（較可靠、較慢）
                else:
                    # 射後不理：仍讀掉緩衝避免累積，但不等待
                    if self._ser.in_waiting:
                        self._ser.read(self._ser.in_waiting)
            except Exception as e:
                print(f"[Arduino] 送出錯誤: {e}")
                self.connected = False

    def tap(self, key):
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
        self._q.put("DOWN:" + _token(key))

    def key_up(self, key):
        self._q.put("UP:" + _token(key))

    def close(self):
        try:
            self._q.put(None)
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self.connected = False
