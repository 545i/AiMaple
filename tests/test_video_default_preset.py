# -*- coding: utf-8 -*-
"""遠端影像的【預設】畫質要等於前端的「快速」預設。

【為什麼要有這支測試】預設值分散在兩個地方:server/config.py 的常數(fps/bitrate)
與 server/video_pipeline.py 的 state(scale/gray),而前端 webapp/src/tabs/
RemoteTab.tsx 的 PRESETS 又各自寫了一份數字。以前伺服器預設是 fps60/25M/原始解析度
—— 那組數字【不對應任何一個預設按鈕】,所以剛連上時四顆按鈕沒有一顆會亮,而且吃掉
25Mbps。這支測試把兩邊釘在一起,任一邊改動而沒同步就會紅。

前端 RemoteTab.tsx 的 PRESETS:
    fast: { scale: 540, fps: 60, bitrate: 8, gray: 0 }
"""
import video_pipeline

# 與 webapp/src/tabs/RemoteTab.tsx 的 PRESETS.fast 逐項對齊
FAST_PRESET = {"scale": 540, "fps": 60, "bitrate": 8, "gray": 0}


def test_default_video_state_is_the_fast_preset():
    st = video_pipeline.state
    got = {k: int(st[k]) for k in FAST_PRESET}
    assert got == FAST_PRESET, f"預設畫質不是「快速」:{got}"
