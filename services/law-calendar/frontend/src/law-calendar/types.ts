import type { CalendarChange, CalendarDay } from '@lawbot/ui'
import type { HoldPage, PublicTaxSchedule } from './api-types'

export interface CalendarSnapshot {
  counts?: CalendarDay[]
  changes: CalendarChange[]
  tax?: PublicTaxSchedule | null
  demo: boolean
  delayed: boolean
  collectedAt: string | null
  verifiedAt: string | null
}

export type CalendarLoader = (month: string, signal: AbortSignal) => Promise<CalendarSnapshot>

export type HoldLoader = (page: number, signal: AbortSignal) => Promise<HoldPage>
