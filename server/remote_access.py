# -*- coding: utf-8 -*-
"""遠端訪客模式：限時短密碼（配合 Cloudflare Quick Tunnel 暴露公網時用）。

設計：
  * 8 碼隨機密碼，字元集去除易混淆字元(0/O/1/l/i)，手機上好輸入。
  * 預設有效 0.5 小時，可隨時延長(+0.5h 或自訂)、撤銷；到期自動失效。
  * 防爆破：密碼有效期間內連續猜錯 10 次 → 鎖 60 秒（公網暴露必備）。
  * 主 token(MAPLE_TOKEN) 不受影響，永遠可用（給自己在 Tailscale 上用）。
  * 安全模型：持短密碼的「訪客」在 main.py 受伺服器端白名單限制——
    只能按 4/←/→ 三鍵，滑鼠/滾輪/剪貼簿/設定 API 一律拒絕。
"""
import hmac
import secrets
import time

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # 去除 0O/1l/i 等易混淆字元
PASSWORD_LEN = 8
MAX_FAILS = 10        # 連續失敗次數上限
LOCK_SECONDS = 60     # 觸發後鎖定秒數

_password = None      # 目前有效的短密碼；None = 尚未產生
_prev_password = None  # 上一組密碼(墓碑)：舊頁面自動重連不計「猜錯」,避免鎖死新訪客
_expires = 0.0        # epoch 秒
_fails = 0
_lock_until = 0.0


def generate(ttl_seconds):
    """產生新短密碼（舊的立即失效），回傳 (password, expires_epoch)。"""
    global _password, _prev_password, _expires, _fails, _lock_until
    if _password:
        _prev_password = _password
    _password = "".join(secrets.choice(ALPHABET) for _ in range(PASSWORD_LEN))
    _expires = time.time() + ttl_seconds
    _fails = 0
    _lock_until = 0.0
    return _password, _expires


def extend(seconds):
    """把現有密碼的有效期往後延。無有效密碼時回 None，否則回新到期 epoch。"""
    global _expires
    if not _password or time.time() > _expires:
        return None
    _expires += seconds
    return _expires


def revoke():
    """立即撤銷密碼（訪客的 WS 看門狗與 MJPEG 串流會隨之中止）。"""
    global _password, _expires
    _password = None
    _expires = 0.0


def _match(pw):
    return bool(_password) and time.time() <= _expires \
        and pw and hmac.compare_digest(str(pw), _password)


def is_valid(pw):
    """登入驗證：猜錯會累計並觸發鎖定（過期/未產生不計，避免舊手機重連灌爆計數）。"""
    global _fails, _lock_until
    now = time.time()
    if not _password or now > _expires:
        return False
    # 正確密碼永遠放行(即使鎖定中)——鎖定只擋「錯誤猜測」,不擋合法持有者登入。
    # 否則攻擊者只要每 60 秒連錯 10 次觸發全域鎖定,就能持續把合法訪客擋在門外
    # (可用性 DoS)。攻擊者不知道正確密碼,故此放行完全不削弱防爆破。
    if _match(pw):
        _fails = 0
        return True
    if now < _lock_until:
        return False                      # 鎖定中：錯誤猜測一律擋，逼攻擊者等待
    # 舊密碼(上一組)重連：拒絕但不計失敗——主人換密碼後,舊訪客頁每 1.5 秒
    # 自動重連,若計失敗會反覆觸發全域鎖定,把「新密碼的合法訪客」鎖在門外。
    if _prev_password and pw and hmac.compare_digest(str(pw), _prev_password):
        return False
    _fails += 1
    if _fails >= MAX_FAILS:
        _lock_until = now + LOCK_SECONDS
        _fails = 0
        print(f"[remote] 密碼連續猜錯 {MAX_FAILS} 次，鎖定 {LOCK_SECONDS} 秒")
    return False


def still_valid(pw):
    """被動檢查（已登入連線的看門狗用）：不累計失敗、不受鎖定影響。"""
    return _match(pw)


def info(include_password=False):
    """目前狀態。include_password 僅主人(主 token)查詢時開啟。"""
    now = time.time()
    active = bool(_password) and now <= _expires
    d = {
        "active": active,
        "remaining_seconds": max(0, int(_expires - now)) if active else 0,
        "locked": now < _lock_until,
    }
    if include_password:
        d["password"] = _password if active else None
    return d
