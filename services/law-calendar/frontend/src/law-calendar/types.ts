import type { CalendarChange, CalendarDay } from '@lawbot/ui'
import type { HoldPage } from './api-types'

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
}

export type CalendarLoader = (month: string, signal: AbortSignal) => Promise<CalendarSnapshot>

export type HoldLoader = (page: number, signal: AbortSignal) => Promise<HoldPage>
