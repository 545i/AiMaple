import { useCallback, useEffect, useRef, useState } from 'react'
import { system } from '../lib/api'

/**
 * 影像來源:只有 WebRTC(WHEP)一條路。
 *
 * 【為什麼不留 MJPEG 備援】舊版連不上 WebRTC 就退 MJPEG,想法是「總比黑屏好」,
 * 實際上是「壞掉了但看起來還在動」:MJPEG 是逐張 JPEG,延遲高、畫面卡,而且畫質
 * 面板調的 bitrate / fps 是 WebRTC 的編碼參數,退到 MJPEG 之後那些滑桿完全沒作用
 * —— 使用者只會覺得「遠端突然變卡而且畫質調不動」,卻沒有任何地方說它降級了。
 * 寧可讓它明確顯示連不上並持續重連,也不要靜默退化成不能用的模式。
 *
 * 【低延遲】收到 track 後把 jitter buffer 壓到最低。不設的話瀏覽器會為了流暢
 *  緩衝數百 ms,遙控時手感會很鈍。
 */
const JITTER_MS = 60
const RETRY_MIN = 700
const RETRY_MAX = 5000

export type VideoKind = 'webrtc' | 'none'

export function useVideo(active: boolean) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const deadRef = useRef(false)
  const [kind, setKind] = useState<VideoKind>('none')
  const [status, setStatus] = useState('未連線')

  const stop = useCallback(() => {
    pcRef.current?.getSenders?.().forEach(s => s.track?.stop())
    pcRef.current?.close()
    pcRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setKind('none')
  }, [])

  /** 一次 WHEP 交握。成功回 RTCPeerConnection,失敗丟例外。 */
  const whepOnce = useCallback(async () => {
    const pc = new RTCPeerConnection()
    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.ontrack = e => {
      const v = videoRef.current
      if (v) { v.srcObject = e.streams[0]; v.play?.().catch(() => {}) }
      const r: any = e.receiver
      try {
        if ('jitterBufferTarget' in r) r.jitterBufferTarget = JITTER_MS
        else if ('playoutDelayHint' in r) r.playoutDelayHint = JITTER_MS / 1000
      } catch { /* 瀏覽器不支援就算了,不影響播放 */ }
      setKind('webrtc'); setStatus('已連線（WebRTC）')
    }
    await pc.setLocalDescription(await pc.createOffer())
    // 等 ICE 收集完再送 offer —— 省掉 trickle,MediaMTX 這樣最穩
    await new Promise<void>(res => {
      if (pc.iceGatheringState === 'complete') return res()
      const chk = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', chk); res()
        }
      }
      pc.addEventListener('icegatheringstatechange', chk)
      setTimeout(res, 2500)     // 保險:ICE 卡住不要無限等
    })
    const url = `${location.protocol}//${location.hostname}:8889/screen/whep`
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription!.sdp,
    })
    if (!r.ok) { pc.close(); throw new Error('whep ' + r.status) }
    await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() })
    return pc
  }, [])

  useEffect(() => {
    deadRef.current = false
    if (!active) { stop(); setStatus('未連線'); return }

    let timer: any = null
    let tries = 0

    const run = async () => {
      if (deadRef.current) return
      try { await system.startVideo() } catch { /* 已在跑就會失敗,無所謂 */ }
      try {
        const pc = await whepOnce()
        if (deadRef.current) { pc.close(); return }
        pcRef.current = pc
        tries = 0
        // 【斷線要自己接回來】沒有備援路線了,重連就是唯一的復原手段
        pc.onconnectionstatechange = () => {
          if (deadRef.current) return
          if (['failed', 'disconnected'].includes(pc.connectionState)) {
            setStatus('影像中斷，重連中…')
            stop(); timer = setTimeout(run, RETRY_MIN)
          }
        }
      } catch {
        tries++
        // 退避重試,永不放棄:服務重啟 / 防火牆放行後會自己接上
        const wait = Math.min(RETRY_MAX, RETRY_MIN * Math.min(tries, 7))
        setStatus(tries <= 3 ? `影像連線中…(${tries})`
                             : `連不上 WebRTC（第 ${tries} 次重試）—— 檢查 MediaMTX 與 8889 埠`)
        timer = setTimeout(run, wait)
      }
    }
    run()
    return () => { deadRef.current = true; if (timer) clearTimeout(timer); stop() }
  }, [active, stop, whepOnce])

  return { videoRef, kind, status }
}
