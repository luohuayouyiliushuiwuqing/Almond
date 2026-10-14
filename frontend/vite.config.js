import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/* 后端 API 目标（almond-webui 默认 127.0.0.1:4000）。
   常用 8888 时：VITE_API_TARGET=http://127.0.0.1:8888 npm run dev
   dev 阶段用 Vite 反代 /api，免 CORS；生产构建产物由 Python 服务端 /app 直接托管（同源，也无 CORS）。 */
const target = process.env.VITE_API_TARGET || 'http://127.0.0.1:4000'

export default defineConfig({
  // 构建产物由 Python 服务端挂在 /app 下，资源必须带 /app 前缀才能正确加载
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target, changeOrigin: true },
    },
  },
  build: {
    // 直接产出到 webui 的 dist/，配合 staticfiles.py 的 /app 挂载
    outDir: '../almond/web/webui/dist',
    emptyOutDir: true,
  },
})
