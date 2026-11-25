import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

const FONT = 'pretendard'
const FONT_VERSION = '1.3.9'
const MIT_PACKAGES = ['react', 'react-dom', 'scheduler']
const RUNTIME = new Set([FONT, ...MIT_PACKAGES])
const NOTICE = `
legalize-kr / admrule-kr
법령·행정규칙 원문: 공공저작물. 각 저장소의 구조·메타데이터: MIT License.
https://github.com/legalize-kr/legalize-kr#라이선스
https://github.com/legalize-kr/admrule-kr#라이선스
`

export function licenseText(root: string): string {
  const read = (file: string) => readFileSync(resolve(root, file), 'utf8')
  const manifest = JSON.parse(read('package.json'))
  const lock: { packages: Record<string, { dev?: boolean; version?: string }> } = JSON.parse(read('package-lock.json'))
  for (const name of Object.keys({ ...manifest.dependencies, ...manifest.optionalDependencies, ...manifest.peerDependencies })) {
    if (!RUNTIME.has(name)) throw new Error(`Missing calendar license for runtime dependency: ${name}`)
  }
  for (const [path, metadata] of Object.entries(lock.packages)) {
    if (path && !metadata.dev && (!path.startsWith('node_modules/') || !RUNTIME.has(path.slice('node_modules/'.length)))) {
      throw new Error(`Missing calendar license for locked runtime dependency: ${path}`)
    }
  }
  for (const name of RUNTIME) {
    const path = `node_modules/${name}`
    const { version } = JSON.parse(read(`${path}/package.json`))
    if (version !== lock.packages[path]?.version || (name === FONT && version !== FONT_VERSION)) {
      throw new Error(`Calendar license version mismatch: ${name}`)
    }
  }
  return [
    `${FONT} ${FONT_VERSION} (SIL Open Font License 1.1)\n${read(`licenses/${FONT}-${FONT_VERSION}.txt`)}`,
    ...MIT_PACKAGES.map(name => `${name} (MIT License)\n${read(`node_modules/${name}/LICENSE`)}`),
    NOTICE,
  ].join('\n\n')
}

export function siteLicenses(root: string): Plugin {
  return {
    name: 'calendar-licenses',
    configureServer(server) {
      const source = licenseText(root)
      server.middlewares.use('/licenses.txt', (_request, response) => {
        response.setHeader('Content-Type', 'text/plain; charset=utf-8')
        response.end(source)
      })
    },
    generateBundle() {
      for (const id of this.getModuleIds()) {
        const fromPackage = id.split('/node_modules/').at(-1)
        if (!fromPackage || fromPackage === id) continue
        const parts = fromPackage.split('/')
        const name = parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0]
        if (!RUNTIME.has(name)) this.error(`Missing calendar license for bundled dependency: ${name}`)
      }
      this.emitFile({ type: 'asset', fileName: 'licenses.txt', source: licenseText(root) })
    },
  }
}
