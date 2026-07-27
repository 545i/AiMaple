# -*- coding: utf-8 -*-
"""獨立音訊管線(P2 音訊)。

與影像完全分開:另跑一支純音訊 ffmpeg，把系統 loopback 聲音編成 Opus 推到
MediaMTX 的 `audio` 路徑;前端用第二條 WHEP(:8889/audio/whep) 只收音訊。
因為不碰 ddagrab，避開了「ddagrab + 音訊同進程」的 filtergraph 死結;也因為
用獨立路徑，不會跟影像搶 `screen` 路徑。影像 ffmpeg 一行都不動 → 音訊壞了
也絕不影響影像。

擷取:soundcard 的 WASAPI loopback(錄「預設輸出正在播的聲音」)。這是非侵入式
的複製,不會靜音/改路由/搶獨占 → 不影響主機本機日常聲音。免安裝虛擬音效線。

關閉:MAPLE_AUDIO=0;soundcard 不可用時 available() 回 False,自動不啟動。
"""
import os
import subprocess
import threading

from config import AUDIO_ENABLED, AUDIO_BITRATE_K
import paths

try:
    import soundcard as sc
    import numpy as np
except Exception:          # 套件缺失/載入失敗 → 音訊自動停用
    sc = None
    np = None

RATE = 48000
CHANNELS = 2
BLOCK = 1024               # ~21ms/塊

_ROOT = paths.ASSETS          # 同 video_pipeline
# ffmpeg 走 paths:打包後它在 exe 旁的素材夾,不在 exe 內部
# (138MB 的執行檔塞進 onefile 會讓每次啟動都解壓它)。
_FFMPEG = paths.bin_path("ffmpeg", "bin", "ffmpeg.exe")
_RTSP = "rtsp://localhost:8554/audio"

_proc = None
_thread = None
_stop = False


def available():
    """能否啟用音訊(開關開 + soundcard 可用 + 抓得到預設輸出)。"""
    if not AUDIO_ENABLED or sc is None:
        return False
    try:
        return sc.default_speaker() is not None
    except Exception:
        return False


def is_running():
    return _proc is not None and _proc.poll() is None


def _ff_args():
    return [_FFMPEG, "-hide_banner", "-loglevel", "error",
            "-thread_queue_size", "1024",
            "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", f"{AUDIO_BITRATE_K}k", "-application", "lowdelay",
            "-f", "rtsp", "-rtsp_transport", "tcp", _RTSP]


def _pump(proc):
    """loopback 錄音 → 寫進這支 ffmpeg 的 stdin。綁定傳入的 proc,不用全域,避免競態。"""
    try:
        spk = sc.default_speaker()
        mic = sc.get_microphone(spk.name, include_loopback=True)
        with mic.recorder(samplerate=RATE, channels=CHANNELS, blocksize=BLOCK) as rec:
            print(f"[Audio] loopback 擷取開始: {spk.name}")
            while not _stop and proc.poll() is None:
                data = rec.record(numframes=BLOCK)          # float32 [-1,1], shape (N, ch)
                pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                try:
                    proc.stdin.write(pcm)
                except (BrokenPipeError, ValueError, OSError, AttributeError):
                    break
    except Exception as e:
        print(f"[Audio] loopback 擷取停止: {e}")


def start():
    """啟動音訊管線(通常與影像 /video/start 一起)。回傳是否成功。"""
    global _proc, _thread, _stop
    if is_running():
        return True
    if not available():
        return False
    try:
        _proc = subprocess.Popen(_ff_args(), cwd=_ROOT, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _stop = False
        _thread = threading.Thread(target=_pump, args=(_proc,), daemon=True)
        _thread.start()
        print(f"[Audio] ffmpeg 啟動 → {_RTSP}  ({AUDIO_BITRATE_K}k Opus)")
        return True
    except Exception as e:
        print(f"[Audio] 啟動失敗: {e}")
        _proc = None
        return False


def stop():
    global _proc, _stop
    _stop = True
    if is_running():
        try:
            _proc.terminate()
            _proc.wait(timeout=3)
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _proc = None
