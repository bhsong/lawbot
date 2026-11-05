/** Phase 1 display models, not the future public API contract. Dates are KST civil dates. */
export type EventKind = 'notice' | 'effective'

export interface CalendarChange {
  id: string
  kind: EventKind
  date: string
  lawName: string
  article: string
  summary: string
  status: string
  before: string | null
  after: string | null
  comparable: boolean
  publishedOn: string | null
  promulgatedOn: string | null
  effectiveOn: string | null
  commentDeadline: string | null
  sourceUrl: string | null
  collectedAt: string | null
  verifiedAt: string | null
}

export interface CalendarSnapshot {
  changes: CalendarChange[]
  demo: boolean
  delayed: boolean
  collectedAt: string | null
  verifiedAt: string | null
}

export type CalendarLoader = (month: string, signal: AbortSignal) => Promise<CalendarSnapshot>
