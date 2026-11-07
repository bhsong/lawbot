import { sseParser } from './sse'
import type { Config } from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

export const NETWORK_ERROR = '서버에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.'

async function detail(res: Response) {
  try {
    const body = await res.json()
    return typeof body?.detail === 'string' ? body.detail : `오류 ${res.status}`
  } catch {
    return `오류 ${res.status}`
  }
}

async function call(path: string, init?: RequestInit) {
  const res = await fetch(path, init)
  if (!res.ok) throw new ApiError(res.status, await detail(res))
  return res
}

function post(path: string, body: unknown, signal?: AbortSignal) {
  return call(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal })
}

export const api = {
  me: async () => (await (await call('/api/me')).json()) as { user: string; role?: 'user' | 'admin' },
  login: (user: string, password: string) => post('/api/login', { user, password }),
  logout: () => post('/api/logout', {}),
  config: async () => (await (await call('/api/config')).json()) as Config,
  feedback: (turn_id: string, label: string, reason?: string) => post('/api/feedback', { turn_id, label, reason }),

  /** Stream one question; calls onEvent for each server event until the response ends. */
  async chat(body: { question: string; model: string; format: string }, signal: AbortSignal, onEvent: (e: string, d: unknown) => void) {
    const res = await post('/api/chat', body, signal)
    const parser = sseParser(onEvent)
    const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(value)
    }
    parser.end()
  },
}
