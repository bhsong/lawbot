import type { CalendarChange, CalendarDay } from '@lawbot/ui'

export interface TaxSchedule {
  items: { id: string; title: string; date: string; targetPeriod: string | null; sourceUrl: string }[]
  confirmedAt: string | null
}

export interface CalendarSnapshot {
  counts?: CalendarDay[]
  changes: CalendarChange[]
  demo: boolean
  delayed: boolean
  collectedAt: string | null
  verifiedAt: string | null
  billCollectedAt?: string | null
  noticeCollectedAt?: string | null
}

export type CalendarLoader = (month: string, signal: AbortSignal) => Promise<CalendarSnapshot>

export type HoldKind = 'effective' | 'notice'
export type HoldLoader = (page: number, signal: AbortSignal, kind: HoldKind) => Promise<import('./api-types').HoldPage>
