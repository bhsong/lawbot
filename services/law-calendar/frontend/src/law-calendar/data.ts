import type { CalendarChange } from '@lawbot/ui'
import type { CalendarLoader, HoldKind } from './types'
import type { CalendarMonth, CalendarStatus, ChangePage, HoldPage, PublicDetail } from './api-types'

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, credentials: 'omit', cache: 'no-store' })
  if (!response.ok) throw new Error('Calendar API unavailable')
  return response.json() as Promise<T>
}

export function loadChange(id: string, signal: AbortSignal): Promise<CalendarChange> {
  return read<PublicDetail>(`/api/law-changes/${encodeURIComponent(id)}`, signal)
}

export async function loadHolds(page: number, signal: AbortSignal, kind: HoldKind, demo = false): Promise<HoldPage> {
  if (import.meta.env.DEV && demo) {
    const { demoHolds } = await import('./demo')
    return demoHolds(page, signal, kind)
  }
  const result = await read<HoldPage>(`/api/law-holds?type=${kind}&page=${page}&page_size=20`, signal)
  if (!Array.isArray(result.items) || result.page !== page || result.page_size !== 20 || result.total < 0
    || (result.items.length === 0 && result.total > (page - 1) * 20)) throw new Error('Invalid hold pagination')
  return result
}

/** Only approved public API data is used outside the explicit development preview. */
export const loadCalendar: CalendarLoader = async (month, signal) => {
  if (import.meta.env.DEV && new URLSearchParams(window.location.search).get('source') !== 'api') {
    const { loadDemo } = await import('./demo')
    return loadDemo(month, signal)
  }
  const [calendar, status] = await Promise.all([
    read<CalendarMonth>(`/api/law-calendar?month=${month}`, signal),
    read<CalendarStatus>('/api/law-calendar/status', signal),
  ])
  const changes: CalendarChange[] = []
  for (let page = 1; ; page++) {
    const result = await read<ChangePage>(`/api/law-events?month=${month}&page=${page}&page_size=100`, signal)
    if (!Array.isArray(result.items) || result.page !== page || result.total < changes.length + result.items.length)
      throw new Error('Invalid calendar pagination')
    changes.push(...result.items)
    if (changes.length === result.total) break
    if (result.items.length === 0 || page >= 100) throw new Error('Incomplete calendar month')
  }
  return { changes, counts: calendar.days, demo: false, ...status }
}
