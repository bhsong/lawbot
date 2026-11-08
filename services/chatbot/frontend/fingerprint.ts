// Source fingerprint of the screen, written to dist/build.json at build time.
// lawbot.web.source_fingerprint computes the same hash and refuses to start when dist is stale.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const FRONTEND = 'services/chatbot/frontend'
export const SOURCES = [
  ...['src', 'index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'fingerprint.ts']
    .map((name) => `${FRONTEND}/${name}`),
  'packages/lawbot-ui/src',
]

function files(root: string, path: string): string[] {
  const full = join(root, path)
  const stat = statSync(full, { throwIfNoEntry: false })
  if (!stat?.isDirectory()) return stat?.isFile() ? [path] : []
  return readdirSync(full, { withFileTypes: true }).flatMap((entry) => {
    const child = join(path, entry.name)
    if (entry.isDirectory()) return files(root, child)
    return statSync(join(root, child), { throwIfNoEntry: false })?.isFile() ? [child] : []
  })
}

/** sha256 over sorted repository-relative POSIX paths and file bytes. */
export function sourceFingerprint(root: string): string {
  const all = SOURCES.flatMap((p) => files(root, p)).map((p) => relative(root, join(root, p)).split(sep).join('/'))
  const hash = createHash('sha256')
  for (const rel of all.sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))) {
    hash.update(rel + '\0')
    hash.update(readFileSync(join(root, rel)))
    hash.update('\0')
  }
  return hash.digest('hex')
}
