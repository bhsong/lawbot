import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const api = process.env.LAW_CALENDAR_API_URL

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: { host: '127.0.0.1', proxy: api ? { '/api': { target: api } } : undefined },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'] },
})
