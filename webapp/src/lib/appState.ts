import { useSyncExternalStore } from 'react'

/**
 * 跨分頁共用的少量 UI 狀態。
 *
 * 【為什麼需要】有些狀態的「擁有者」與「使用者」不是同一個元件:
 *   watching  遠端頁的開關,但真正影響的是 StageStream 要不要收影像
 *   layout    遠端頁設定按鍵佈局方向,但用它的是 VirtualPad
 *   sens      遠端頁設定靈敏度,但用它的是 TouchPad
 * 全部塞進 App 再一層層傳 props,ControlPanel 是動態選 Body 的,傳起來很醜;
 * 而各自留本地狀態則會「切走再切回就重置」(注視畫面就踩過這個)。
 *
 * 用 useSyncExternalStore 寫一個 30 行的小 store 就夠了,不必為此裝狀態管理套件。
 * 有 localStorage key 的欄位會自動持久化(與舊版同名,設定不會因為改版丟失)。
 */
export interface AppState {
  watching: boolean
  layout: 'auto' | 'port' | 'land'
  sens: number
}

const PERSIST: Partial<Record<keyof AppState, string>> = {
  layout: 'maple_layout',
  sens: 'maple_sens',
}

const num = (v: string | null, d: number) => (v == null || v === '' ? d : Number(v))

let state: AppState = {
  watching: true,
  layout: (localStorage.getItem('maple_layout') as AppState['layout']) || 'auto',
  sens: num(localStorage.getItem('maple_sens'), 3),
}

const subs = new Set<() => void>()

export function setAppState(patch: Partial<AppState>) {
  state = { ...state, ...patch }
  for (const [k, key] of Object.entries(PERSIST)) {
    const v = (patch as any)[k]
    if (v !== undefined && key) localStorage.setItem(key, String(v))
  }
  subs.forEach(f => f())
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }

export function useAppState(): AppState {
  return useSyncExternalStore(subscribe, () => state, () => state)
}
