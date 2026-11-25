import type { CalendarChange } from '@lawbot/ui'
import { isDemoMode, readData, readIndex } from '../site-data'
import type { CalendarSnapshot } from './types'
import type { CalendarMonth, CalendarStatus, ChangePage, HoldPage, PublicDetail, PublicTaxSchedule } from './api-types'
import { demoHolds, loadDemo } from './demo'

export function loadChange(id: string, signal: AbortSignal): Promise<CalendarChange> {
  return readData<PublicDetail>(signal, 'law-changes', id)
}

export async function loadHolds(page: number, signal: AbortSignal, demo = false): Promise<HoldPage> {
  if (import.meta.env.DEV && demo) {
    return demoHolds(page, signal)
  }
  const result = await readData<HoldPage>(signal, 'law-holds')
  const page_size = 20
  if (result.items.length !== result.total) throw new Error('Incomplete hold list')
  return { ...result, items: result.items.slice((page - 1) * page_size, page * page_size), page, page_size }
}

export async function loadCalendar(month: string, signal: AbortSignal, includeTax = false): Promise<CalendarSnapshot> {
  if (import.meta.env.DEV && isDemoMode()) {
    return loadDemo(month, signal)
  }
  const [index, status] = await Promise.all([
    readIndex(signal),
    readData<CalendarStatus>(signal, 'law-calendar', 'status'),
  ])
  if (!index.months.includes(month)) return { changes: [], counts: [], demo: false, ...status }
  const [calendar, changes, tax] = await Promise.all([
    readData<CalendarMonth>(signal, 'law-calendar', month),
    readData<ChangePage>(signal, 'law-events', month),
    includeTax ? readData<PublicTaxSchedule>(signal, 'tax-schedule', month).catch(() => null) : undefined,
  ])
  if (changes.items.length !== changes.total) throw new Error('Incomplete calendar month')
  return { changes: changes.items, counts: calendar.days, tax, demo: false, ...status }
}

export function loadCalendarWithTax(month: string, signal: AbortSignal) {
  return loadCalendar(month, signal, true)
}
