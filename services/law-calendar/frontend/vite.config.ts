import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: { host: '127.0.0.1' },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'] },
})
