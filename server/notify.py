# -*- coding: utf-8 -*-
"""Telegram 通知(掛機警報)。

用 Bot API sendMessage 送到指定 chat。背景執行緒送出、不阻塞偵測/請求路徑;
失敗只印 log(通知屬盡力而為,不影響主功能)。無第三方依賴(內建 urllib)。
"""
import json
import threading
import urllib.request

from config import TELEGRAM_ENABLED, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def telegram(text):
    """背景送出 Telegram 訊息。回傳是否已排入送出(非是否成功)。"""
    if not (TELEGRAM_ENABLED and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return False

    def _send():
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            # 注意:主控台是 cp950,print emoji 會拋 UnicodeEncodeError(曾把
            # 「已送出」誤印成失敗),log 一律轉 ascii 安全字串。
            print(f"[notify] Telegram 已送出: {ascii(text)}")
        except Exception as e:
            print(f"[notify] Telegram 送出失敗: {e}")

    threading.Thread(target=_send, daemon=True).start()
    return True
