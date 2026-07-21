"""
MapleVhid 的 Python 綁定 (ctypes)，直接對驅動下 IOCTL。

用途：讓 maple 的 FastAPI server 可以用虛擬 HID 驅動取代 Arduino 鍵盤 /
km.dll 滑鼠。需要以系統管理員身分執行 Python 行程。

    from driver.maple_vhid import MapleVhid

    hid = MapleVhid()
    hid.key_down(0x04)      # 'a'
    hid.key_up(0x04)
    hid.mouse_move(10, -5)
    hid.mouse_click(MapleVhid.BTN_LEFT)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct

# --- Win32 常數 ---------------------------------------------------------

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FILE_DEVICE_UNKNOWN = 0x00000022
METHOD_BUFFERED = 0
FILE_WRITE_ACCESS = 0x0002


def _ctl_code(device_type: int, function: int, method: int, access: int) -> int:
    return (device_type << 16) | (access << 14) | (function << 2) | method


def _maple_ioctl(index: int) -> int:
    return _ctl_code(FILE_DEVICE_UNKNOWN, index, METHOD_BUFFERED, FILE_WRITE_ACCESS)


IOCTL_KEY_DOWN = _maple_ioctl(0x900)
IOCTL_KEY_UP = _maple_ioctl(0x901)
IOCTL_KEY_RESET = _maple_ioctl(0x902)
IOCTL_KEYBOARD_REPORT = _maple_ioctl(0x903)
IOCTL_MOUSE_UPDATE = _maple_ioctl(0x910)
IOCTL_MOUSE_RESET = _maple_ioctl(0x911)
IOCTL_MOUSE_REPORT = _maple_ioctl(0x912)
IOCTL_GET_STATE = _maple_ioctl(0x920)

DEVICE_PATH = r"\\.\MapleVhid"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.CreateFileW.restype = wt.HANDLE
_kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
    wt.DWORD, wt.DWORD, wt.HANDLE,
]
_kernel32.DeviceIoControl.restype = wt.BOOL
_kernel32.DeviceIoControl.argtypes = [
    wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]


class MapleVhidError(OSError):
    pass


class MapleVhid:
    """虛擬 HID 鍵盤 + 滑鼠。"""

    # Modifier
    MOD_LCTRL = 0x01
    MOD_LSHIFT = 0x02
    MOD_LALT = 0x04
    MOD_LGUI = 0x08
    MOD_RCTRL = 0x10
    MOD_RSHIFT = 0x20
    MOD_RALT = 0x40
    MOD_RGUI = 0x80

    # Mouse buttons
    BTN_LEFT = 0x01
    BTN_RIGHT = 0x02
    BTN_MIDDLE = 0x04
    BTN_X1 = 0x08
    BTN_X2 = 0x10

    # LED
    LED_NUM = 0x01
    LED_CAPS = 0x02
    LED_SCROLL = 0x04

    def __init__(self, path: str = DEVICE_PATH):
        handle = _kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle is None or handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            raise MapleVhidError(
                f"無法開啟 {path} (error {err})；請確認驅動已安裝且以系統管理員執行"
            )
        self._handle = handle

    # --- 底層 ---------------------------------------------------------

    def _ioctl(self, code: int, payload: bytes = b"", out_len: int = 0) -> bytes:
        in_buf = ctypes.create_string_buffer(payload) if payload else None
        out_buf = ctypes.create_string_buffer(out_len) if out_len else None
        returned = wt.DWORD(0)

        ok = _kernel32.DeviceIoControl(
            self._handle,
            code,
            ctypes.cast(in_buf, ctypes.c_void_p) if in_buf else None,
            len(payload),
            ctypes.cast(out_buf, ctypes.c_void_p) if out_buf else None,
            out_len,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise MapleVhidError(f"IOCTL 0x{code:08X} 失敗 (error {ctypes.get_last_error()})")

        return out_buf.raw[: returned.value] if out_buf else b""

    # --- 鍵盤 ---------------------------------------------------------

    def key_down(self, usage: int) -> None:
        self._ioctl(IOCTL_KEY_DOWN, struct.pack("<B", usage & 0xFF))

    def key_up(self, usage: int) -> None:
        self._ioctl(IOCTL_KEY_UP, struct.pack("<B", usage & 0xFF))

    def key_reset(self) -> None:
        self._ioctl(IOCTL_KEY_RESET)

    def keyboard_report(self, modifiers: int = 0, keys: bytes = b"") -> None:
        """直接送一份完整 report；keys 最多 6 個 usage。"""
        padded = bytes(keys[:6]).ljust(6, b"\x00")
        self._ioctl(IOCTL_KEYBOARD_REPORT, struct.pack("<BBB6s", 1, modifiers, 0, padded))

    # --- 滑鼠 ---------------------------------------------------------

    def mouse_update(self, buttons_down=0, buttons_up=0, dx=0, dy=0, wheel=0, hwheel=0) -> None:
        self._ioctl(
            IOCTL_MOUSE_UPDATE,
            struct.pack("<BBhhbb", buttons_down, buttons_up, dx, dy, wheel, hwheel),
        )

    def mouse_move(self, dx: int, dy: int) -> None:
        self.mouse_update(dx=dx, dy=dy)

    def mouse_down(self, buttons: int) -> None:
        self.mouse_update(buttons_down=buttons)

    def mouse_up(self, buttons: int) -> None:
        self.mouse_update(buttons_up=buttons)

    def mouse_click(self, buttons: int) -> None:
        self.mouse_down(buttons)
        self.mouse_up(buttons)

    def mouse_wheel(self, vertical: int = 0, horizontal: int = 0) -> None:
        self.mouse_update(wheel=vertical, hwheel=horizontal)

    def mouse_reset(self) -> None:
        self._ioctl(IOCTL_MOUSE_RESET)

    # --- 狀態 ---------------------------------------------------------

    def get_state(self) -> dict:
        raw = self._ioctl(IOCTL_GET_STATE, out_len=9)
        modifiers, keys, mouse_buttons, leds = struct.unpack("<B6sBB", raw)
        return {
            "modifiers": modifiers,
            "keys": list(keys),
            "mouse_buttons": mouse_buttons,
            "leds": leds,
        }

    # --- 生命週期 -----------------------------------------------------

    def close(self) -> None:
        if getattr(self, "_handle", None):
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "MapleVhid":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self):
        self.close()
