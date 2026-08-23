import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

// ===== 全域禁止手勢縮放 =====
// 【逐行照搬舊版 web/index.html(959~966 行)】新版一直沒有這一段,這是舊版手機端
// 正常、新版不正常的實際差異之一。
//
// iOS Safari【會無視 viewport 的 user-scalable=no】,兩指一放就被它當成縮放手勢
// 接走:瀏覽器接管後會把進行中的觸控 touchcancel 掉,TouchGesture 跟著 reset,
// 兩指輕點的右鍵因此永遠送不出去;雙擊縮放也會讓單指的點擊判斷變得不穩。
// 這幾行必須在 document 層攔,元素上的 touch-action:none 擋不到 gesture* 事件。
;['gesturestart', 'gesturechange', 'gestureend'].forEach(ev =>
  document.addEventListener(ev, e => e.preventDefault(), { passive: false }))
document.addEventListener('touchmove', e => {
  if (e.touches.length > 1) e.preventDefault()
}, { passive: false })
let _lastTouchEnd = 0
document.addEventListener('touchend', e => {
  const now = Date.now()
  if (now - _lastTouchEnd <= 300) e.preventDefault()
  _lastTouchEnd = now
}, { passive: false })
document.addEventListener('dblclick', e => e.preventDefault(), { passive: false })

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>
)
