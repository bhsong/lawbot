import { loadCalendar } from './data'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

test('all public pages are read without browser credentials', async () => {
  vi.stubEnv('DEV', false)
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('status') ? { collectedAt: null, verifiedAt: null, delayed: false }
      : url.includes('law-events') ? { items: [{ id: url.includes('page=2') ? 'two' : 'one' }], total: 2, page: url.includes('page=2') ? 2 : 1 }
        : { days: [] },
  )))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const result = await loadCalendar('2026-11', controller.signal)
  expect(result.changes.map(item => item.id)).toEqual(['one', 'two'])
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining('page=2'), expect.objectContaining({ credentials: 'omit', signal: controller.signal }))
})

test('HTTP errors and truncated pagination do not become an empty or partial month', async () => {
  vi.stubEnv('DEV', false)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 503 })))
  await expect(loadCalendar('2026-11', new AbortController().signal)).rejects.toThrow()
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('law-events') ? { items: [], total: 3, page: 1 } : url.includes('status') ? {} : { days: [] },
  ))))
  await expect(loadCalendar('2026-11', new AbortController().signal)).rejects.toThrow('Incomplete')
})
