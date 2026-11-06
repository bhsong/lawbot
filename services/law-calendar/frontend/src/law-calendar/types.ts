/** Display models for public records and development fixtures. API contracts are generated separately. */
import type { CalendarDay, PublicAssembly, PublicNoticeContent } from './api-types'

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
  assembly?: PublicAssembly | null
  noticeContent?: PublicNoticeContent | null
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
