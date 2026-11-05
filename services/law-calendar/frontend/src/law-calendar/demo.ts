import { todayKst, shiftDay } from './dates'
import type { CalendarChange, CalendarLoader, HoldLoader } from './types'

/** Synthetic UI fixtures only; this marker must never occur in production assets. */
const DEMO_ID = 'LAW_CALENDAR_SYNTHETIC_ONLY'
const day = todayKst()

function sample(kind: 'notice' | 'effective', index: number): CalendarChange {
  return {
    id: `${DEMO_ID}-${kind}-${index}`, kind, date: day,
    lawName: '[예시] 가상 업무절차법', article: `제${index + 1}조`,
    summary: kind === 'notice' ? '신청 방식에 전자문서를 추가하는 예고 예시' : '신청 처리 문구가 달라지는 시행 예시',
    status: kind === 'notice' ? '예고 중 · 확정 아님' : '시행 예시',
    before: '① 신청은 서면으로 제출한다.\n② 접수 내역을 기록한다.',
    after: '① 신청은 서면 또는 전자문서로 제출한다.\n② 접수 내역을 기록한다.',
    comparable: true, publishedOn: kind === 'notice' ? day : null,
    promulgatedOn: kind === 'effective' ? shiftDay(day, -30) : null,
    effectiveOn: kind === 'effective' ? day : null,
    commentDeadline: kind === 'notice' ? shiftDay(day, 30) : null,
    sourceUrl: null, collectedAt: null, verifiedAt: null,
  }
}

export const loadDemo: CalendarLoader = async (month, signal) => {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  const changes = [
    ...Array.from({ length: 10 }, (_, i) => sample('notice', i)),
    sample('effective', 0),
    { ...sample('effective', 1), comparable: false, before: null, after: null, summary: '비교 원문 확인이 필요한 예시', status: '확인 필요' },
    { ...sample('effective', 2), before: null, summary: '조문 신설 표시 예시' },
    { ...sample('effective', 3), after: null, summary: '조문 삭제 표시 예시' },
  ].filter((event) => event.date.startsWith(month))
  return { changes, demo: true, delayed: false, collectedAt: null, verifiedAt: null }
}

export const demoHolds: HoldLoader = async (page, signal) => {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  return { items: page === 1 ? [{ id: `${DEMO_ID}-held`, lawName: '[예시] 가상 업무절차법', article: '제9조',
    reason: '대통령령 등 별도로 정하는 시행일을 확인해야 합니다.', sourceUrl: 'https://www.law.go.kr/', collectedAt: day }] : [],
    total: 1, page, page_size: 20 }
}
