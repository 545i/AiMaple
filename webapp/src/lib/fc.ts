// 浮動客戶端(Electron 殼)才有的橋接。preload 用 contextBridge 把這些動作曝露成
// window.fc(見 client/preload.js);純瀏覽器開這頁時 window.fc 不存在(hasFc=false),
// 相關控制項(半透明滑桿、設定鈕等)就不渲染。
interface Fc {
  getSettings: () => Promise<{ alpha: number; topmost: boolean; platform?: string; notices?: string[] }>
  setAlpha: (v: number) => Promise<unknown>
  setTopmost: (on: boolean) => Promise<unknown>
  setOverlay: (on: boolean) => Promise<unknown>
  openSettings: () => Promise<unknown>
  reload: () => Promise<unknown>
}

export const fc: Fc | undefined = (window as unknown as { fc?: Fc }).fc
export const hasFc = !!fc
