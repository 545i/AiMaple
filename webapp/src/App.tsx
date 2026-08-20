import { useEffect, useState } from 'react'
import { hasToken } from './lib/token'
import { useAppState } from './lib/appState'
import { useInput } from './hooks/useInput'
import { useVideo } from './hooks/useVideo'
import { useDesktopInput } from './hooks/useDesktopInput'
import { useDrawer } from './hooks/useDrawer'
import { useRuneOverlay } from './hooks/useRuneOverlay'
import { useNavTrace } from './hooks/useNavTrace'
import { TopHud } from './components/TopHud'
import { StageStream } from './components/StageStream'
import { ControlPanel } from './components/ControlPanel'
import { VirtualPad } from './components/VirtualPad'
import { TouchPad } from './components/TouchPad'
import { QuickBar } from './components/QuickBar'
import { Login } from './components/Login'
import './App.css'

export type TabId = 'patrol' | 'remote' | 'rent' | 'idle' | 'hw' | 'dev'

/**
 * 【觸控裝置要用能力偵測,不能用寬度】大螢幕手機/平板橫放時寬度會超過 900px,
 * 用寬度判斷會把它當成電腦 → 虛擬按鍵整組消失,而那台裝置根本沒有實體鍵鼠。
 * 舊版用的就是這個判準(IS_TOUCH),這裡照抄。
 * isWide 之後只負責【版面排列】(多欄 vs 單欄),與輸入方式脫鉤。
 */
const IS_TOUCH = navigator.maxTouchPoints > 0 || 'ontouchstart' in window

/**
 * 版面只有一個真相來源:抽屜的段位(snap)。
 *   collapsed 全收 — 遊戲全螢幕,可操作(虛擬按鍵/觸控板/鍵盤都上)
 *   half      半拉 — 上半看遊戲、下半操作控制台(最常用)
 *   full      滿版 — 專心操作控制台
 * 舊版有 view-login/dash/remote 三種 body class 再加抽屜開合,兩套語意互打
 * (「抽屜收合了卻還停在儀表板」)。這裡不會有第二個真相來源。
 */
export default function App() {
  const [authed, setAuthed] = useState(hasToken())
  const [tab, setTab] = useState<TabId>('patrol')
  const [padOn, setPadOn] = useState(true)
  const drawer = useDrawer('half')
  const [editPad, setEditPad] = useState(false)
  // 電腦端改用「左側常駐 rail + 可展開左側面板」,不走底部三段式抽屜(drawer 在電腦端閒置)。
  // deskOpen 只管左側面板展開/收合;預設收合(只留 rail),遊戲畫面一開始就完整露出。
  const isDesk = !IS_TOUCH
  const [deskOpen, setDeskOpen] = useState(false)
  // 收起整條 rail(列表):連舞台讓給 rail 的 56px 一起收回、畫面補滿整寬(.rail-hidden)
  const [railHidden, setRailHidden] = useState(false)

  const [isWide, setIsWide] = useState(() => matchMedia('(min-width:900px)').matches)
  const [autoPortrait, setAutoPortrait] = useState(() => matchMedia('(orientation:portrait)').matches)
  useEffect(() => {
    const a = matchMedia('(min-width:900px)'), b = matchMedia('(orientation:portrait)')
    const f1 = () => setIsWide(a.matches), f2 = () => setAutoPortrait(b.matches)
    a.addEventListener('change', f1); b.addEventListener('change', f2)
    return () => { a.removeEventListener('change', f1); b.removeEventListener('change', f2) }
  }, [])

  // 電腦端進全螢幕就自動收合左側面板(畫面全露);離開不強制展開,交回使用者
  useEffect(() => {
    if (!isDesk) return
    const on = () => { if (document.fullscreenElement) setDeskOpen(false) }
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [isDesk])

  // 佈局方向 / 靈敏度 / 注視開關的擁有者是遠端頁,但用它們的是這裡的
  // VirtualPad / TouchPad / StageStream。走共用 store,不必層層傳 props,
  // 也不會因為切走分頁就重置(注視開關踩過這個坑)。
  const { layout, sens, watching, runeOverlay, navTrace: traceOn } = useAppState()
  // 符文偵測框:開發頁開的,畫在 StageStream 上(輪詢只在開著時發生)
  const overlay = useRuneOverlay(runeOverlay && authed)
  // 導航行動軌跡:同樣疊在遠端畫面上,不另外拉圖
  const trace = useNavTrace(traceOn && authed)
  const portrait = layout === 'auto' ? autoPortrait : layout === 'port'
  // 手機是直的、但使用者鎖定橫向佈局 → 把畫面轉 90° 填滿(舊版的 rotate 模式)。
  // 只在手機成立:電腦端本來就是橫的,轉了反而壞掉。
  const rotate = !isWide && !portrait && autoPortrait


  // 滿版時純粹在操作控制台,不需要【輸入通道】—— 但【影像】不能跟著斷。
  // 【踩過的坑】原本 useVideo 也吃 canPlay,抽屜一拉到滿版就整條 WebRTC 關掉,
  // 收回來要重跑一次 WHEP 交握;交握失敗就退 MJPEG,畫面變超卡而且 bitrate/fps
  // (WebRTC 編碼參數)完全調不動。抽屜只是蓋住畫面,不是離開遠端。
  const canPlay = authed && drawer.snap !== 'full'
  const input = useInput(canPlay)
  const video = useVideo(authed && watching)

  // 電腦端實體鍵鼠映射:滑鼠絕對座標 + e.code 鍵位。游標移出影像會自動放開所有鍵。
  const { over } = useDesktopInput(input, !IS_TOUCH && canPlay, video.videoRef)


  // 手機橫向且抽屜全收 = 純操作,連 HUD 都收掉
  const minimal = IS_TOUCH && !autoPortrait && drawer.snap === 'collapsed'
  const playing = drawer.snap === 'collapsed'      // 虛擬按鍵只在全收時出現,半拉沒空間

  if (!authed) return <Login onDone={() => setAuthed(true)} />

  return (
    /* 抽屜高度是版面的唯一真相,用 CSS 變數往下發:舞台可視範圍、提示列位置都靠它。
       各自寫死段位比例的話,拖曳中就會對不上(半拉時影像被蓋掉一半正是這樣來的)。 */
    <div style={{ '--panel-h': drawer.panelH,
                  '--stage-bottom': drawer.stageBottom } as React.CSSProperties}
         className={`app snap-${drawer.snap} ${isWide ? 'wide' : 'narrow'}`
        + ` ${IS_TOUCH ? 'touch' : 'desk'} ${minimal ? 'minimal' : ''} ${rotate ? 'rotate' : ''}`
        + ` ${drawer.dragH != null ? 'dragging' : ''}`
        + ` ${isDesk && railHidden ? 'rail-hidden' : ''}`
        + ` ${IS_TOUCH && portrait && playing ? 'port-play' : ''}`}>
      <StageStream video={video} cursor={input.cursor} overlay={overlay} trace={trace}
                   hint={!IS_TOUCH && canPlay && !over ? '滑鼠移到遊戲畫面上即可操控（移開自動釋放）' : ''} />

      {IS_TOUCH && portrait && playing && <div className="padbg" />}
      {IS_TOUCH && playing && <TouchPad input={input} sensitivity={sens} active />}
      <VirtualPad input={input} portrait={portrait} edit={editPad}
                  visible={playing && padOn && IS_TOUCH} />

      {!minimal && (
        <TopHud snap={drawer.snap} wsOk={input.connected} isDesk={isDesk}
                onToggleSnap={() => drawer.setSnap(drawer.snap === 'full' ? 'collapsed' : 'full')} />
      )}

      {/* 快捷列在右上 —— 虛擬按鍵層是 inset:0 佈滿整個畫面,右下角一定擋到 */}
      {playing && (
        <QuickBar isTouch={IS_TOUCH}
                  padOn={padOn} onTogglePad={() => setPadOn(v => !v)}
                  editPad={editPad} onToggleEditPad={() => setEditPad(v => !v)}
                  onRelease={() => input.releaseAll()}
                  onOpenPanel={() => drawer.setSnap('half')} />
      )}

      <ControlPanel isWide={isWide} minimal={minimal} tab={tab} onTab={setTab}
                    snap={drawer.snap} height={drawer.panelH} dragging={drawer.dragH != null}
                    handlers={drawer.handlers}
                    isDesk={isDesk} deskOpen={deskOpen}
                    onDeskToggle={() => setDeskOpen(o => !o)} onDeskOpen={() => setDeskOpen(true)}
                    railHidden={railHidden}
                    onRailHide={() => setRailHidden(true)} onRailShow={() => setRailHidden(false)} />
    </div>
  )
}
