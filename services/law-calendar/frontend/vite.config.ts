import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { siteLicenses } from './site-build.ts'

const root = import.meta.dirname
const site = resolve(root, process.env.LAW_CALENDAR_SITE ?? '../data/site')
const shared = resolve(import.meta.dirname, '../../../packages/lawbot-ui/src')

export default defineConfig({
  plugins: [react(), siteLicenses(root), {
    name: 'calendar-local-data',
    configureServer(server) {
      server.middlewares.use('/data', async (request, response) => {
        const path = request.url?.split('?')[0]
        if (!path || !/^\/(?:[a-z0-9_-]+\/)?[a-z0-9_-]+\.json$/.test(path)) {
          response.statusCode = 404
          response.end()
          return
        }
        try {
          const data = await readFile(resolve(site, 'data', '.' + path))
          response.setHeader('Content-Type', 'application/json; charset=utf-8')
          response.end(data)
        } catch (error) {
          response.statusCode = (error as NodeJS.ErrnoException).code === 'ENOENT' ? 404 : 500
          response.end()
        }
      })
    },
  }],
  base: '/',
  resolve: { alias: { '@lawbot/ui': shared }, dedupe: ['react', 'react-dom'] },
  server: {
    host: '127.0.0.1',
    fs: { allow: [import.meta.dirname, shared] },
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: {
    environment: 'jsdom', globals: true, setupFiles: ['src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', '../../../packages/lawbot-ui/src/**/*.test.{ts,tsx}'],
    alias: { '@testing-library/react': resolve(import.meta.dirname, 'node_modules/@testing-library/react') },
  },
})
