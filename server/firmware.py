# -*- coding: utf-8 -*-
"""Arduino 韌體燒錄 + 測試訊號。供中控頁「寫入 Arduino 代碼」與「測試訊號」使用。

--------------------------------------------------------------------------
為什麼是「內嵌 .hex + avrdude」而不是帶編譯器
--------------------------------------------------------------------------
    韌體 .hex                28 KB   ← 內嵌在 exe(使用者要求韌體不可外放)
    avrdude.exe + .conf     6.7 MB   ← 放素材夾 bin/avrdude/
    AVR 編譯器工具鏈        271 MB   ← 【不打包】只有開發機需要
所以 .ino 在開發機用 arduino-cli 預先編成 .hex(見 scripts/build.ps1),執行端只負責
把現成的 .hex 燒進板子。

--------------------------------------------------------------------------
Leonardo / Pro Micro 的燒錄眉角(這類板子與 Uno 不同,踩過會很困惑)
--------------------------------------------------------------------------
這類板子平時以「應用程式」的 USB 位址出現;要燒錄必須先讓它進 bootloader,方式是對
序列埠開一個 1200 baud 連線再關掉(所謂 "1200 baud touch")。板子接著會【換一個
新的 COM 埠號】重新列舉,avrdude 要對那個新埠燒。所以流程是:
    1. 記下現在有哪些埠
    2. 對目標埠做 1200 baud touch
    3. 輪詢等「新出現的埠」(通常 1~3 秒)
    4. 對新埠跑 avrdude
少了第 3 步直接對原埠燒,會得到 "programmer is not responding"。
"""
import glob
import os
import subprocess
import time

import paths

FQBN_MCU = "atmega32u4"        # Leonardo / Pro Micro
BAUD = 57600                   # Caterina bootloader 的燒錄速率
TOUCH_BAUD = 1200              # 觸發進 bootloader 的魔術數字
PORT_WAIT = 8.0                # 等新埠出現的上限
FLASH_TIMEOUT = 90.0


def _avrdude():
    """回 (avrdude.exe, avrdude.conf);缺檔回 (None, None)。"""
    exe = paths.bin_path("avrdude", "bin", "avrdude.exe")
    conf = paths.bin_path("avrdude", "etc", "avrdude.conf")
    if os.path.exists(exe) and os.path.exists(conf):
        return exe, conf
    return None, None


def available():
    """能不能燒錄。回 (ok, 原因)。"""
    exe, _conf = _avrdude()
    if not exe:
        return False, f"找不到 avrdude(應在 {paths.bin_path('avrdude', 'bin', 'avrdude.exe')})"
    if not os.path.exists(paths.firmware_hex()):
        return False, "exe 內找不到韌體 .hex(打包時漏了?)"
    return True, "ok"


def list_ports():
    """目前的序列埠 [{'port','desc','hwid'}, ...]。pyserial 缺席時回空清單。"""
    try:
        from serial.tools import list_ports as lp
    except Exception:
        return []
    out = []
    for p in lp.comports():
        out.append({"port": p.device, "desc": p.description or "",
                    "hwid": p.hwid or ""})
    return out


def _port_names():
    return {p["port"] for p in list_ports()}


def guess_port():
    """猜 Arduino 在哪個埠。優先看描述/VID(Arduino 官方 VID 0x2341、SparkFun 0x1B4F)。"""
    cands = list_ports()
    for p in cands:
        h = (p["hwid"] or "").upper()
        d = (p["desc"] or "").lower()
        if "2341" in h or "1B4F" in h or "arduino" in d or "leonardo" in d \
                or "pro micro" in d:
            return p["port"]
    return cands[0]["port"] if len(cands) == 1 else ""


def firmware_info():
    """韌體資訊:大小、來源檔頭幾行(讓中控頁能確認燒的是哪一版)。"""
    hexp, srcp = paths.firmware_hex(), paths.firmware_src()
    info = {"hex_exists": os.path.exists(hexp),
            "hex_bytes": os.path.getsize(hexp) if os.path.exists(hexp) else 0,
            "src_exists": os.path.exists(srcp), "title": ""}
    if info["src_exists"]:
        try:
            with open(srcp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip(" *\t\r\n")
                    if s and not s.startswith("/"):
                        info["title"] = s[:80]
                        break
        except Exception:
            pass
    return info


def _touch_1200(port):
    """1200 baud touch:讓 Leonardo/Pro Micro 跳進 bootloader。"""
    try:
        import serial
    except Exception as e:
        return f"缺 pyserial: {e!r}"
    try:
        s = serial.Serial(port, TOUCH_BAUD)
        s.dtr = False          # 有些板子靠 DTR 拉低才會重開機進 bootloader
        time.sleep(0.05)
        s.close()
        return ""
    except Exception as e:
        # 開不起來不一定是失敗:板子可能已經在 bootloader、或埠被主程式佔用
        return f"{type(e).__name__}: {e}"


def _wait_new_port(before, timeout=PORT_WAIT):
    """等一個「原本沒有」的新埠出現(板子重新列舉)。回埠名或 ''。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.25)
        now = _port_names()
        new = now - before
        if new:
            return sorted(new)[0]
    return ""


def flash(port="", release_fn=None, log=None):
    """把內嵌的 .hex 燒進板子。回 dict(ok, port, boot_port, msg, output)。

    release_fn:燒錄前用來放掉主程式對序列埠的佔用(否則 1200 baud touch 開不了埠)。
    """
    def _log(m):
        if log:
            log(m)
        print(f"[fw] {m}")

    ok, why = available()
    if not ok:
        return {"ok": False, "port": port, "msg": why, "output": ""}

    target = port or guess_port()
    if not target:
        return {"ok": False, "port": "", "output": "",
                "msg": "找不到序列埠。板子插上了嗎?(可在下拉選單手動指定)"}

    if release_fn:
        try:
            release_fn()
            _log("已放開主程式的序列埠佔用")
            time.sleep(0.4)
        except Exception as e:
            _log(f"放開序列埠失敗(仍繼續): {e!r}")

    before = _port_names()
    _log(f"對 {target} 做 1200 baud touch → 進 bootloader")
    terr = _touch_1200(target)
    if terr:
        _log(f"touch 回報 {terr}(不一定是失敗,繼續等新埠)")

    boot = _wait_new_port(before)
    if boot:
        _log(f"bootloader 出現在 {boot}")
    else:
        boot = target
        _log(f"沒有新埠出現,直接對 {target} 燒(板子可能已在 bootloader)")

    exe, conf = _avrdude()
    args = [exe, "-C", conf, "-v", "-p", FQBN_MCU, "-c", "avr109",
            "-P", boot, "-b", str(BAUD), "-D",
            "-U", f"flash:w:{paths.firmware_hex()}:i"]
    _log("avrdude 開始燒錄…")
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=FLASH_TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "") + (r.stderr or "")
        good = r.returncode == 0
        # avrdude 的成功訊息在 stderr,且不同版本用詞不同,故用 returncode 為主、
        # 關鍵字為輔(有些版本 returncode 0 但其實沒寫進去)
        if good and "bytes of flash verified" not in out and "avrdude done" not in out.lower():
            good = False
        msg = "燒錄完成" if good else f"燒錄失敗(avrdude exit={r.returncode})"
        _log(msg)
        return {"ok": good, "port": target, "boot_port": boot, "msg": msg,
                "output": out[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "port": target, "boot_port": boot, "output": "",
                "msg": f"燒錄逾時(>{FLASH_TIMEOUT:.0f}s)"}
    except Exception as e:
        return {"ok": False, "port": target, "boot_port": boot, "output": "",
                "msg": f"燒錄例外: {e!r}"}


# ---------- 測試訊號 ----------
# 刻意【不按任何會影響遊戲的鍵】。預設用 numpad 5(N5):遊戲裡通常沒綁,
# 而且在記事本/瀏覽器裡看得到字元,一眼就知道 HID 通了。
TEST_TOKENS = {
    "n5": "N5",          # 小鍵盤 5 —— 預設,最安全
    "shift": "SHIFT",    # 修飾鍵:不會輸入字元,但能用「HID 有回應」驗證
    "left": "LEFT",
    "right": "RIGHT",
}


def test_signal(keyboard, token="n5", times=3, gap=0.25):
    """送幾次測試訊號,回 dict(ok, sent, replies, msg)。

    keyboard 是 main 裡那個 ArduinoKeyboard 實例 —— 直接用它送,才是驗證「主程式這條
    路徑」真的通;另外開一個序列埠連線去測會測到不同的東西(而且會搶埠)。
    """
    tok = TEST_TOKENS.get(str(token).lower())
    if not tok:
        return {"ok": False, "sent": 0, "replies": [], "msg": f"不支援的測試鍵 {token!r}"}
    if keyboard is None or not getattr(keyboard, "connected", False):
        return {"ok": False, "sent": 0, "replies": [],
                "msg": "Arduino 未連線(序列埠沒開或沒插板子)"}
    sent, replies = 0, []
    for i in range(max(1, min(10, int(times)))):
        try:
            rep = _send_raw(keyboard, tok)
            sent += 1
            replies.append(rep)
        except Exception as e:
            replies.append(f"ERR {e!r}")
        if i < times - 1:
            time.sleep(gap)
    good = sum(1 for r in replies if str(r).strip().upper().startswith("OK"))
    return {"ok": good > 0, "sent": sent, "ok_count": good, "replies": replies,
            "msg": (f"送出 {sent} 次,{good} 次收到 OK" if good
                    else "板子沒有回 OK —— 韌體燒好了嗎?協定對嗎?")}


def _send_raw(keyboard, token):
    """送一個 token 並取回板子回應。

    走 ArduinoKeyboard.probe():序列埠只由它的 worker 執行緒讀寫,從這裡直接
    readline 會與 worker 搶同一個回應,兩邊都可能拿到對方的。"""
    fn = getattr(keyboard, "probe", None)
    if not callable(fn):
        raise RuntimeError("ArduinoKeyboard 沒有 probe(),無法取回應")
    return fn(token)
