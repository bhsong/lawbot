/** Display models shared by the calendar views. */
export interface CalendarChange {
  eventId?: string
  id: string
  date: string
  lawName: string
  article: string
  summary: string
  status: string
  before: string | null
  after: string | null
  comparable: boolean
  promulgatedOn: string | null
  effectiveOn: string | null
  sourceUrl: string | null
  collectedAt: string | null
  verifiedAt: string | null
}

export interface CalendarDay {
  event_on: string
  effective_count: number
}

