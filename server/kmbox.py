# -*- coding: utf-8 -*-
"""km.dll (KMBox 類硬體) 滑鼠橋接。

函數簽章依 DLL函數.txt 官方文件，並以 ctypes argtypes/restype 明確宣告，
避免傳參型別錯誤。本專案只用它控滑鼠（鍵盤交給 Arduino）。

要點：
  * km.dll 預設為「非同步模式」：送出滑鼠指令後立即返回，硬體端有 256 級佇列
    依序處理 → 適合低延遲遠端，不需等待回應。
  * MoveR 為相對移動，需在 Windows「滑鼠內容 → 指標選項」中**取消「提高指標
    精確度」**，否則實際位移與傳入值對不上。
  * 文件中並無滾輪(wheel)函數，故本橋接不提供滾輪。
  * 所有呼叫加鎖，避免多執行緒同時進 DLL。
"""
import ctypes
import os
import threading

from config import KM_DLL, KM_VID, KM_PID

# 滑鼠鍵編號（文件：1=左 2=右 3=中 4-8=側鍵）
_BTN = {"left": 1, "right": 2, "middle": 3,
        "side1": 4, "side2": 5, "side3": 6, "side4": 7, "side5": 8}


class KMouse:
    def __init__(self, dll_path=KM_DLL):
        self._lock = threading.Lock()
        self.connected = False
        here = os.path.dirname(os.path.abspath(__file__))
        self._dll_path = os.path.join(here, dll_path)
        self._dll = None

    def _bind_signatures(self):
        """依官方文件宣告使用到的函數簽章。"""
        d = self._dll
        d.OpenDeviceByID.argtypes = [ctypes.c_short, ctypes.c_short]
        d.OpenDeviceByID.restype = ctypes.c_int
        d.IsOpen.restype = ctypes.c_int
        d.Close.argtypes = []
        # 移動
        d.MoveR.argtypes = [ctypes.c_short, ctypes.c_short]                      # 相對移動
        d.MoveRD.argtypes = [ctypes.c_short, ctypes.c_short,
                             ctypes.c_ubyte, ctypes.c_ubyte]                     # 曲線平滑相對移動
        d.MoveTo.argtypes = [ctypes.c_ushort, ctypes.c_ushort]                   # 絕對移動
        # 按鍵（1-8）
        d.MouseButtonDown.argtypes = [ctypes.c_ubyte]
        d.MouseButtonUp.argtypes = [ctypes.c_ubyte]
        d.MouseButtonClick.argtypes = [ctypes.c_ubyte, ctypes.c_int, ctypes.c_int]

    def start(self):
        try:
            self._dll = ctypes.WinDLL(self._dll_path)
            self._bind_signatures()
        except Exception as e:
            print(f"[KM] 載入 DLL 失敗: {e}")
            return False
        try:
            ret = self._dll.OpenDeviceByID(KM_VID, KM_PID)
            self.connected = ret == 1
        except Exception as e:
            print(f"[KM] 開啟裝置失敗: {e}")
            self.connected = False
        print("[KM] 已連線" if self.connected else "[KM] 裝置未連線（滑鼠停用）")
        return self.connected

    # ===== 滑鼠移動 =====
    def move_relative(self, dx, dy):
        """相對移動（最快，硬體端非同步佇列處理）。"""
        if not self.connected:
            return
        with self._lock:
            self._dll.MoveR(int(dx), int(dy))

    def move_relative_smooth(self, dx, dy, delay=8, delta=10):
        """曲線平滑相對移動：分多步、走二次曲線，較擬真。用於視角轉動。"""
        if not self.connected:
            return
        with self._lock:
            self._dll.MoveRD(int(dx), int(dy), int(delay) & 0xFF, int(delta) & 0xFF)

    def move_to(self, x, y):
        if not self.connected:
            return
        with self._lock:
            self._dll.MoveTo(int(x) & 0xFFFF, int(y) & 0xFFFF)

    # ===== 滑鼠按鍵 =====
    def button_down(self, button="left"):
        idx = _BTN.get(button)
        if not self.connected or idx is None:
            return
        with self._lock:
            self._dll.MouseButtonDown(idx)

    def button_up(self, button="left"):
        idx = _BTN.get(button)
        if not self.connected or idx is None:
            return
        with self._lock:
            self._dll.MouseButtonUp(idx)

    def click(self, button="left", min_delay=0, max_delay=0):
        idx = _BTN.get(button)
        if not self.connected or idx is None:
            return
        with self._lock:
            self._dll.MouseButtonClick(idx, int(min_delay), int(max_delay))

    def close(self):
        if self._dll and self.connected:
            try:
                self._dll.Close()
            except Exception:
                pass
        self.connected = False
