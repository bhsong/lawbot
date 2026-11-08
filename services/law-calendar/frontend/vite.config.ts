import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const api = process.env.LAW_CALENDAR_API_URL
const shared = resolve(import.meta.dirname, '../../../packages/lawbot-ui/src')

export default defineConfig({
  plugins: [react()],
  base: '/',
  resolve: { alias: { '@lawbot/ui': shared }, dedupe: ['react', 'react-dom'] },
  server: {
    host: '127.0.0.1',
    fs: { allow: [import.meta.dirname, shared] },
    proxy: api ? { '/api': { target: api } } : undefined,
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: {
    environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', '../../../packages/lawbot-ui/src/**/*.test.{ts,tsx}'],
    alias: { '@testing-library/react': resolve(import.meta.dirname, 'node_modules/@testing-library/react') },
  },
})
