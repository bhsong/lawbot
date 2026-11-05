/** Display models for public records and development fixtures. API contracts are generated separately. */
export type EventKind = 'notice' | 'effective'

export interface CalendarChange {
  eventId?: string
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
  counts?: { event_on: string; notice_count: number; effective_count: number }[]
  changes: CalendarChange[]
  demo: boolean
  delayed: boolean
  collectedAt: string | null
  verifiedAt: string | null
}

export type CalendarLoader = (month: string, signal: AbortSignal) => Promise<CalendarSnapshot>
