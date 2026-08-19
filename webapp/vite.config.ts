import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 建置產物直接輸出到 web/dist/,由 FastAPI 送出(見 server/main.py 的 index 路由)。
// base 用相對路徑,這樣不管掛在哪個路徑下都能載到資源。
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../web/dist', emptyOutDir: true, sourcemap: false },
  server: {
    port: 5173,
    // 開發時把所有後端端點 proxy 到跑著的服務,前端改動即時熱更新。
    proxy: Object.fromEntries(
      ['/status','/ws','/video','/config','/windows','/window','/client','/exp','/face',
       '/idle','/input','/job','/map','/minimap','/monitor','/nav','/remote','/revive',
       '/rune','/fiona','/arduino','/calib','/clipboard','/guest']
        .map(p => [p, { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true }])
    ),
  },
})
