import { loadCalendar, loadChange, loadHolds } from './data'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

const status = { collectedAt: null, verifiedAt: null, delayed: false }
const index = { months: ['2026-11'], erpRuleDates: [] }

test('reads the exported month, combined event list and escaped change detail from the same origin', async () => {
  vi.stubEnv('DEV', false)
  const files: Record<string, object> = {
    '/data/law-calendar/months.json': index,
    '/data/law-calendar/status.json': status,
    '/data/law-calendar/2026-11.json': { days: [{ event_on: '2026-11-05', effective_count: 2 }] },
    '/data/law-events/2026-11.json': { items: [{ id: 'one' }, { id: 'two' }], total: 2 },
    '/data/law-changes/example%20id.json': { id: 'example id', diff: ['+after'] },
  }
  const fetch = vi.fn(async (url: string) => url in files ? Response.json(files[url]) : new Response(null, { status: 404 }))
  vi.stubGlobal('fetch', fetch)
  const { signal } = new AbortController()
  const result = await loadCalendar('2026-11', signal)
  expect(result.changes.map(item => item.id)).toEqual(['one', 'two'])
  expect(result.counts?.[0].effective_count).toBe(2)
  await expect(loadChange('example id', signal)).resolves.toMatchObject({ diff: ['+after'] })
  expect(fetch).toHaveBeenCalledWith('/data/law-changes/example%20id.json', expect.objectContaining({ credentials: 'same-origin', signal }))
})

test('an unindexed month shows zero results without asking for missing month files', async () => {
  vi.stubEnv('DEV', false)
  const fetch = vi.fn(async (url: string) => url.endsWith('months.json') ? Response.json(index)
    : url.endsWith('status.json') ? Response.json(status) : new Response(null, { status: 404 }))
  vi.stubGlobal('fetch', fetch)
  await expect(loadCalendar('2030-01', new AbortController().signal)).resolves.toMatchObject({ changes: [], counts: [], delayed: false })
  expect(fetch.mock.calls.map(([url]) => url)).not.toContain('/data/law-events/2030-01.json')
})

test('a broken export is not shown as an empty month', async () => {
  vi.stubEnv('DEV', false)
  const { signal } = new AbortController()
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 503 })))
  await expect(loadCalendar('2026-11', signal)).rejects.toThrow()
  vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json(
    url.endsWith('months.json') ? index : url.includes('law-events') ? { items: [], total: 3 } : { days: [] },
  )))
  await expect(loadCalendar('2026-11', signal)).rejects.toThrow('Incomplete')
})

test('the second holds page shows the remaining items from the combined file', async () => {
  const items = Array.from({ length: 21 }, (_, i) => ({ id: String(i) }))
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path === '/data/law-holds.json'
    ? Response.json({ items, total: 21, page: 1, page_size: 21 }) : new Response(null, { status: 404 })))
  await expect(loadHolds(2, new AbortController().signal)).resolves.toEqual({
    items: [{ id: '20' }], total: 21, page: 2, page_size: 20,
  })
})
