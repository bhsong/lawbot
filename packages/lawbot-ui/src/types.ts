/** Display models shared by the calendar views. */
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

export interface CalendarDay {
  event_on: string
  notice_count: number
  effective_count: number
}

