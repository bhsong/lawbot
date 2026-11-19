import { loadErp, loadErpRules } from './data'

afterEach(() => vi.unstubAllGlobals())

test('ERP data is read with same-origin credentials so an access gate cookie reaches the API', async () => {
  const fetch = vi.fn(async () => Response.json({ state: 'ok' }))
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await loadErp(signal)
  await loadErpRules('2026-11-19', signal)
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/erp-updates', expect.objectContaining({ credentials: 'same-origin', signal }))
  expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining('/api/erp-updates/v2?'), expect.objectContaining({ credentials: 'same-origin', signal }))
})
