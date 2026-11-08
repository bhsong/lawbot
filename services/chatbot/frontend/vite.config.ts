// Vite: build → dist/ (served by lawbot-web on the same port). `npm run dev` proxies /api to LAWBOT_WEB_URL.
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { sourceFingerprint } from './fingerprint'

const api = process.env.LAWBOT_WEB_URL // e.g. http://127.0.0.1:<lawbot-web port>; never hard-coded
const repo = resolve(import.meta.dirname, '../../..')
const shared = resolve(repo, 'packages/lawbot-ui/src')

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'lawbot-build-json',
      apply: 'build',
      generateBundle() {
        const source = sourceFingerprint(repo)
        this.emitFile({ type: 'asset', fileName: 'build.json', source: JSON.stringify({ source }) + '\n' })
      },
    },
  ],
  resolve: { alias: { '@lawbot/ui': shared }, dedupe: ['react', 'react-dom'] },
  server: {
    host: '127.0.0.1',
    fs: { allow: [import.meta.dirname, shared] },
    proxy: api ? { '/api': { target: api, changeOrigin: false } } : undefined,
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'] },
})
