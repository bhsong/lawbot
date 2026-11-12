import { act, renderHook, waitFor } from '@testing-library/react'
import { useChatStream } from './useChatStream'

const enc = new TextEncoder()

function sseResponse(chunks: string[], { hold = false } = {}) {
  let ctrl: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c
      chunks.forEach((x) => c.enqueue(enc.encode(x)))
      if (!hold) c.close()
    },
  })
  const res = new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
  return { res, close: () => ctrl.close(), fail: (e: unknown) => ctrl.error(e) }
}

const DONE = [
  'event: status\ndata: {"text": "⏳"}\n\n',
  'event: answer\ndata: {"text": "답"}\n\n',
  'event: done\ndata: {"text": "답변", "finish": "stop", "turn_id": null}\n\n',
]

afterEach(() => vi.unstubAllGlobals())

test('send streams one turn to done', async () => {
  const fetch = vi.fn().mockResolvedValue(sseResponse(DONE).res)
  vi.stubGlobal('fetch', fetch)
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  await act(() => result.current.send('질문', 'pro', 'concise'))
  expect(fetch).toHaveBeenCalledWith('/api/chat', expect.objectContaining({ method: 'POST' }))
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ question: '질문', model: 'pro', format: 'concise' })
  expect(result.current.turns).toHaveLength(1)
  expect(result.current.turns[0]).toMatchObject({ question: '질문', text: '답변', done: true })
  expect(result.current.busy).toBe(false)
})

test('stop aborts the request and marks the turn stopped', async () => {
  const fetch = vi.fn((_url: string, init: RequestInit) => {
    const { res, fail } = sseResponse(['event: answer\ndata: {"text": "부분"}\n\n'], { hold: true })
    init.signal!.addEventListener('abort', () => fail(new DOMException('aborted', 'AbortError'))) // like real fetch
    return Promise.resolve(res)
  })
  vi.stubGlobal('fetch', fetch)
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  let pending: Promise<void>
  act(() => {
    pending = result.current.send('질문', 'pro', 'concise')
  })
  await waitFor(() => expect(result.current.turns.at(0)?.text).toBe('부분'))
  await act(async () => {
    result.current.stop()
    await pending
  })
  expect(fetch.mock.calls[0][1].signal!.aborted).toBe(true)
  expect(result.current.turns[0]).toMatchObject({ done: true, stopped: true, text: '부분' })
  expect(result.current.busy).toBe(false)
})

test('synchronous sends share one active request and allow another after it ends', async () => {
  const held = sseResponse(DONE, { hold: true })
  const fetch = vi.fn().mockResolvedValueOnce(held.res).mockResolvedValueOnce(sseResponse(DONE).res)
  vi.stubGlobal('fetch', fetch)
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  const pending: Promise<void>[] = []
  act(() => {
    pending.push(result.current.send('첫 질문', 'pro', 'full'))
    pending.push(result.current.send('중복 질문', 'pro', 'full'))
  })
  held.close()
  await act(async () => { await Promise.all(pending) })
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(result.current.turns.map((t) => t.question)).toEqual(['첫 질문'])
  await act(() => result.current.send('다음 질문', 'pro', 'full'))
  expect(fetch).toHaveBeenCalledTimes(2)
})

test('unmount aborts the pending stream', async () => {
  const held = sseResponse([], { hold: true })
  const fetch = vi.fn((_url: string, init: RequestInit) => {
    init.signal!.addEventListener('abort', () => held.fail(new DOMException('aborted', 'AbortError')))
    return Promise.resolve(held.res)
  })
  vi.stubGlobal('fetch', fetch)
  const { result, unmount } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  let pending: Promise<void>
  act(() => { pending = result.current.send('질문', 'pro', 'full') })
  unmount()
  const aborted = fetch.mock.calls[0][1].signal!.aborted
  if (!aborted) held.close()
  await pending!
  expect(aborted).toBe(true)
})

test('a malformed event cancels the response body', async () => {
  const cancel = vi.fn()
  const body = new ReadableStream({
    start(controller) { controller.enqueue(enc.encode('event: answer\ndata: invalid\n\n')) },
    cancel,
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)))
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  await act(() => result.current.send('질문', 'pro', 'full'))
  expect(cancel).toHaveBeenCalledOnce()
  expect(result.current.turns[0]).toMatchObject({ done: true, error: expect.any(String) })
})

test('401 calls onUnauthorized, 400 shows the server message', async () => {
  const onUnauthorized = vi.fn()
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response('{"detail": "로그인이 필요합니다."}', { status: 401 }))
    .mockResolvedValueOnce(new Response('{"detail": "질문을 입력해 주세요."}', { status: 400 }))
  vi.stubGlobal('fetch', fetch)
  const { result } = renderHook(() => useChatStream({ onUnauthorized }))
  await act(() => result.current.send('q', 'pro', 'full'))
  expect(onUnauthorized).toHaveBeenCalledOnce()
  await act(() => result.current.send('q', 'pro', 'full'))
  expect(result.current.turns.at(-1)).toMatchObject({ done: true, error: '질문을 입력해 주세요.' })
})

test('network failure becomes an error message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  await act(() => result.current.send('q', 'pro', 'full'))
  expect(result.current.turns[0].error).toMatch('서버에 연결하지 못했습니다')
})

test('stream that ends without done is reported', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(['event: answer\ndata: {"text": "부"}\n\n']).res))
  const { result } = renderHook(() => useChatStream({ onUnauthorized: vi.fn() }))
  await act(() => result.current.send('q', 'pro', 'full'))
  expect(result.current.turns[0]).toMatchObject({ done: true, text: '부' })
  expect(result.current.turns[0].error).toMatch('연결이 끊겼습니다')
})
