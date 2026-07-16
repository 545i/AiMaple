# -*- coding: utf-8 -*-
"""軟體滑鼠備援（Windows SendInput）。

當 KMBox(km.dll) 未連線時使用，讓觸控板仍可操控。
注意：這是「軟體注入」，反作弊軟體可能偵測得到 —— 要硬體等級(反作弊安全)
請改用 KMBox，或改走 Arduino HID 滑鼠(需擴充韌體)。
"""
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32

MOUSEEVENTF_MOVE       = 0x0001
MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040

_DOWN = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN, "middle": MOUSEEVENTF_MIDDLEDOWN}
_UP   = {"left": MOUSEEVENTF_LEFTUP,   "right": MOUSEEVENTF_RIGHTUP,   "middle": MOUSEEVENTF_MIDDLEUP}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", _MOUSEINPUT)]


def _send(flags, dx=0, dy=0):
    inp = _INPUT(type=0, mi=_MOUSEINPUT(dx, dy, 0, flags, 0, None))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


class SoftMouse:
    connected = True   # 軟體版永遠可用
    software = True

    def start(self):
        print("[SoftMouse] 使用軟體滑鼠備援(SendInput) — 反作弊可偵測，僅供無 KMBox 時使用")
        return True

    def move_relative(self, dx, dy):
        _send(MOUSEEVENTF_MOVE, int(dx), int(dy))

    def move_relative_smooth(self, dx, dy, delay=8, delta=10):
        _send(MOUSEEVENTF_MOVE, int(dx), int(dy))

    def button_down(self, button="left"):
        f = _DOWN.get(button);  _send(f) if f else None

    def button_up(self, button="left"):
        f = _UP.get(button);  _send(f) if f else None

    def click(self, button="left", min_delay=0, max_delay=0):
        d, u = _DOWN.get(button), _UP.get(button)
        if d and u: _send(d); _send(u)

    def close(self):
        pass
