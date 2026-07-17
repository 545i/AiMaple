# -*- coding: utf-8 -*-
"""設定主機(被控端)的 Windows 剪貼簿，讓操作端能把文字複製過來。

用 ctypes Win32 剪貼簿 API(CF_UNICODETEXT)，支援中文，無需額外套件。
"""
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]


def set_clipboard(text):
    """把 text 設為主機剪貼簿內容。成功回 True。"""
    text = str(text)
    if not _user32.OpenClipboard(None):
        return False
    try:
        _user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = _kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _kernel32.GlobalUnlock(h)
        _user32.SetClipboardData(CF_UNICODETEXT, h)   # 交給系統，勿再釋放 h
        return True
    except Exception as e:
        print(f"[Clipboard] 設定失敗: {e}")
        return False
    finally:
        _user32.CloseClipboard()
