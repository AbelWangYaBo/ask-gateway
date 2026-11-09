import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 构建产物直接落到后端静态目录，由 FastAPI SPA 兜底托管
const BACKEND_DIST = fileURLToPath(
  new URL('../src/ask_gateway/server/static/dist', import.meta.url),
)

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: BACKEND_DIST,
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/admin': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/v1': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    // 默认 about:blank 是 opaque origin，jsdom 会禁用 localStorage
    environmentOptions: {
      jsdom: { url: 'http://localhost/' },
    },
    setupFiles: ['src/__tests__/setup.ts'],
    include: ['src/**/__tests__/**/*.test.ts'],
  },
})
