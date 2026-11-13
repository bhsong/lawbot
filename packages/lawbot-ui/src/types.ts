/** Display models shared by the calendar views. */
export type EventKind = 'notice' | 'effective' | 'bill'

const dateRoles: Record<string, string> = {
  notice_start: '예고 시작일',
  notice_end: '예고 종료일',
  notice_period: '예고 시작·종료일',
  bill_proposed: '발의·제안일',
  bill_decided: '의결일',
  bill_proposed_decided: '발의·제안 및 의결일',
}

export function dateRoleLabel(role: string) {
  return dateRoles[role] ?? '공식 일정'
}

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
  assembly?: CalendarAssembly | null
  noticeContent?: CalendarNoticeContent | null
}

export interface CalendarDay {
  event_on: string
  notice_count: number
  effective_count: number
  bill_count?: number
}

interface CalendarAssembly {
  agency: string | null
  billNo: string
  dateRole: string
  noticeStart: string | null
  noticeEnd: string | null
  billProposed: string | null
  billDecided: string | null
  committeeDecided: string | null
  committeeResult: string | null
  judiciaryDecided: string | null
  judiciaryResult: string | null
}

interface CalendarNoticeContent {
  summary: string | null
  pdfUrl: string | null
}
