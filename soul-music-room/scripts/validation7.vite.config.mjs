// Acceptance-only same-origin session transport; the normal config is untouched.
import { defineConfig } from 'vite'
import existing from '../vite.config.ts'

export default defineConfig({
  ...existing,
  server: {
    ...existing.server,
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      ...existing.server.proxy,
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true },
    },
  },
})
