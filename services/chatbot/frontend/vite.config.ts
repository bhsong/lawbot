// Vite: build → dist/ (served by lawbot-web on the same port). `npm run dev` proxies /api to LAWBOT_WEB_URL.
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const api = process.env.LAWBOT_WEB_URL // e.g. http://127.0.0.1:<lawbot-web port>; never hard-coded

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: api ? { '/api': { target: api, changeOrigin: false } } : undefined },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'] },
})
