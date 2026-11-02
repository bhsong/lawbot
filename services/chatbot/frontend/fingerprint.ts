// Source fingerprint of the screen, written to dist/build.json at build time.
// lawbot.web.source_fingerprint computes the same hash and refuses to start when dist is stale.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

export const SOURCES = ['src', 'index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'fingerprint.ts']

function files(root: string, path: string): string[] {
  const full = join(root, path)
  if (!statSync(full).isDirectory()) return [path]
  return readdirSync(full).flatMap((name) => files(root, join(path, name)))
}

/** sha256 over sorted posix paths and file bytes: path NUL bytes NUL ... */
export function sourceFingerprint(root: string): string {
  const all = SOURCES.flatMap((p) => files(root, p)).map((p) => relative(root, join(root, p)).split(sep).join('/'))
  const hash = createHash('sha256')
  for (const rel of all.sort()) {
    hash.update(rel + '\0')
    hash.update(readFileSync(join(root, rel)))
    hash.update('\0')
  }
  return hash.digest('hex')
}
