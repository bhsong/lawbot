import { loadErp, loadErpRules } from './data'

afterEach(() => vi.unstubAllGlobals())

test('reads the ERP feed and prefers the exact date when its file is available', async () => {
  const fetch = vi.fn(async (path: string) => {
    if (path === '/data/erp-updates.json') return Response.json({ state: 'ok', relatedLaws: ['가상법'] })
    if (path.endsWith('months.json')) return Response.json({ months: [], erpRuleDates: ['2026-11-18', '2026-11-19'] })
    if (path === '/data/erp-rules/2026-11-19.json') return Response.json({ state: 'ok', asOfDate: '2026-11-19' })
    return new Response(null, { status: 503 })
  })
  vi.stubGlobal('fetch', fetch)
  const { signal } = new AbortController()
  await expect(loadErp(signal)).resolves.toMatchObject({ relatedLaws: ['가상법'] })
  await expect(loadErpRules('2026-11-19', signal)).resolves.toMatchObject({ asOfDate: '2026-11-19' })
  expect(fetch).toHaveBeenCalledWith('/data/erp-rules/2026-11-19.json', expect.objectContaining({ credentials: 'same-origin', signal }))
})

test('after a missing date, uses the newest available past edition and never a future edition', async () => {
  const fetch = vi.fn(async (path: string) => {
    if (path.endsWith('months.json')) return Response.json({
      months: [], erpRuleDates: ['2026-11-17', '2026-11-18', '2026-11-20'],
    })
    if (path.endsWith('2026-11-17.json')) return Response.json({ state: 'ok', asOfDate: '2026-11-17' })
    return new Response(null, { status: 404 })
  })
  vi.stubGlobal('fetch', fetch)
  await expect(loadErpRules('2026-11-19', new AbortController().signal)).resolves.toMatchObject({ asOfDate: '2026-11-17' })
  expect(fetch.mock.calls.map(([path]) => path)).not.toContain('/data/erp-rules/2026-11-20.json')
})

test('a server error is not silently replaced by older ERP data', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('months.json')
    ? Response.json({ months: [], erpRuleDates: ['2026-11-18', '2026-11-19'] })
    : new Response(null, { status: 503 })))
  await expect(loadErpRules('2026-11-19', new AbortController().signal)).rejects.toThrow()
})

test('a clock before the export window does not use a future edition', async () => {
  const fetch = vi.fn(async (path: string) => path.endsWith('months.json')
    ? Response.json({ months: [], erpRuleDates: ['2026-11-20'] })
    : Response.json({ state: 'ok', asOfDate: '2026-11-20' }))
  vi.stubGlobal('fetch', fetch)
  await expect(loadErpRules('2026-11-19', new AbortController().signal)).resolves.toEqual({
    state: 'missing', asOfDate: '2026-11-19',
  })
})
