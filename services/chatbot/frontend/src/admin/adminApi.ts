import { useEffect, useRef, useState } from 'react'
import { NETWORK_ERROR } from '../api'

export class AdminApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly detail: string) {
    super(detail)
  }
}

const notices: Record<string, string> = {
  bad_origin: '요청 출처를 확인할 수 없어 저장하지 않았습니다. 같은 주소에서 다시 열어 주세요.',
  leak_test: '시험 데이터와 겹쳐 승격할 수 없습니다.',
  leak_val: '검증 데이터와 겹쳐 승격할 수 없습니다.',
  leak_eval: '평가 전용 문항과 겹쳐 승격할 수 없습니다.',
  unknown_article: '현재 조문에서 근거 조문을 찾을 수 없습니다.',
}

function safeDetail(detail: unknown) {
  return typeof detail === 'string' && !/[\r\n]/.test(detail) ? detail : ''
}

export function adminMessage(error: unknown): string {
  if (!(error instanceof AdminApiError)) return NETWORK_ERROR
  if (error.status === 403 && (!error.code || error.code === 'admin_required')) {
    return '관리자 권한이 필요하거나 이 요청에 대한 권한이 없습니다.'
  }
  const notice = notices[error.code]
  if (!notice) return error.detail || `요청을 처리하지 못했습니다. (오류 ${error.status})`
  return error.detail && error.detail !== notice ? `${notice} ${error.detail}` : notice
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/admin${path}`, init)
  if (!response.ok) {
    const body: { code?: unknown; detail?: unknown } | null = await response.json().catch(() => null)
    const code = typeof body?.code === 'string' ? body.code : ''
    throw new AdminApiError(response.status, code, response.status < 500 || code ? safeDetail(body?.detail) : '')
  }
  return response.json() as Promise<T>
}

export function adminGet<T>(path: string, signal?: AbortSignal) {
  return request<T>(path, { signal, cache: 'no-store' })
}

export function calendarPath(path: string) {
  return `/api/admin/calendar/${path}`
}

export function useAdminRead<T>(load: (signal: AbortSignal) => Promise<T>, onUnauthorized: () => void) {
  const [state, setState] = useState<{ data?: T; error?: unknown }>({})
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setState({})
    load(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setState({ data }) },
      (error: unknown) => {
        if (controller.signal.aborted) return
        if (error instanceof AdminApiError && error.status === 401) onUnauthorized()
        else setState({ error })
      },
    )
    return () => controller.abort()
  }, [load, onUnauthorized, revision])
  return { ...state, retry: () => setRevision((value) => value + 1) }
}

export function useAdminMutation(onUnauthorized: () => void) {
  const pending = useRef<{ path: string; payload: string; body: string } | null>(null)
  const active = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => () => active.current?.abort(), [])

  function invalidate() {
    pending.current = null
    setError('')
  }

  async function run<T>(path: string, fields: Record<string, unknown>): Promise<T | undefined> {
    if (active.current) return
    const payload = JSON.stringify(fields)
    if (pending.current?.path !== path || pending.current.payload !== payload) {
      pending.current = { path, payload, body: JSON.stringify({ ...fields, idempotency_key: crypto.randomUUID() }) }
    }
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError('')
    try {
      const result = await request<T>(path, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: pending.current.body, signal: controller.signal,
      })
      pending.current = null
      if (!controller.signal.aborted) return result
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof AdminApiError && error.status < 500 && error.status !== 429) pending.current = null
      if (error instanceof AdminApiError && error.status === 401) onUnauthorized()
      else setError(adminMessage(error))
    } finally {
      active.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return { run, busy, error, invalidate }
}
