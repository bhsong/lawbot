import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LawCalendarPage from './LawCalendarPage'
import type { CalendarChange, CalendarLoader, CalendarSnapshot } from './types'

const change: CalendarChange = {
  id: 'notice', kind: 'notice', date: '2026-11-05', lawName: '[예시] 가상법', article: '제1조',
  summary: '예시 변경 요약', status: '예고 중 · 확정 아님', before: '신청은 서면으로 한다.',
  after: '신청은 전자문서로 한다.', comparable: true, publishedOn: '2026-11-05',
  promulgatedOn: null, effectiveOn: null, commentDeadline: '2026-12-01', sourceUrl: null,
  collectedAt: null, verifiedAt: null,
}
const snapshot: CalendarSnapshot = {
  changes: [change, { ...change, id: 'effective', kind: 'effective', summary: '예시 시행 요약', status: '시행 예시' }],
  demo: true, delayed: false, collectedAt: null, verifiedAt: null,
}
const loader: CalendarLoader = async () => snapshot

test('public calendar has no chatbot navigation or authentication requests', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  try {
    render(<LawCalendarPage loader={loader} initialDate="2026-11-05" />)
    await screen.findByText('예시 변경 요약')
    expect(screen.queryByRole('link', { name: /Lawbot 챗봇/ })).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  } finally {
    vi.unstubAllGlobals()
  }
})

test('selection updates both panels, empty dates, month navigation and today', async () => {
  render(<LawCalendarPage loader={loader} initialDate="2026-11-05" />)
  expect(await screen.findByText('예시 변경 요약')).toBeInTheDocument()
  expect(screen.getByText('예시 시행 요약')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /^2026년 11월 6일/ }))
  expect(screen.getByText('이 날짜에 게시된 입법예고가 없습니다.')).toBeInTheDocument()
  expect(screen.getByText('이 날짜에 시행되는 조문이 없습니다.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '다음 달' }))
  expect(screen.getByRole('heading', { name: '2026년 12월' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '오늘' }))
  expect(screen.getByRole('button', { current: 'date' })).toHaveAttribute('aria-pressed', 'true')
})

test('keyboard crosses month boundaries and leaves a single date in the tab order', async () => {
  render(<LawCalendarPage loader={loader} initialDate="2026-11-30" />)
  await screen.findByRole('grid')
  screen.getByRole('button', { name: /^2026년 11월 30일/ }).focus()
  await userEvent.keyboard('{ArrowRight}')
  expect(screen.getByRole('button', { name: /^2026년 12월 1일/ })).toHaveFocus()
  await userEvent.keyboard('{Home}')
  expect(screen.getByRole('button', { name: /^2026년 11월 29일/ })).toHaveFocus()
  const dates = within(screen.getByRole('grid')).getAllByRole('button')
  expect(dates.filter((button) => button.tabIndex === 0)).toHaveLength(1)
})

test('loading, retry, delay and verification absence are explicit', async () => {
  let fail = true
  const retry = vi.fn(async () => {
    if (fail) throw new Error('internal error must stay private')
    return { ...snapshot, delayed: true }
  })
  render(<LawCalendarPage loader={retry} initialDate="2026-11-05" />)
  expect(screen.getByRole('status')).toHaveTextContent('불러오는 중')
  expect(await screen.findByRole('alert')).toHaveTextContent('불러오지 못했습니다')
  expect(screen.queryByText(/internal error/)).not.toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole('button', { name: '다시 시도' }))
  expect(await screen.findByText(/데이터 갱신이 지연/)).toBeInTheDocument()
  expect(screen.getByText(/검증 시각: 확인되지 않음/)).toBeInTheDocument()
})

test('production provider is empty and makes no API or model requests', async () => {
  vi.stubEnv('DEV', false)
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  try {
    render(<LawCalendarPage initialDate="2026-11-05" />)
    expect(await screen.findByText(/승인 데이터 연결 전/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/불러오는 중/)).not.toBeInTheDocument())
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByText('예시 변경 요약')).not.toBeInTheDocument()
  } finally {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  }
})

test('long lists paginate instead of silently dropping changes', async () => {
  const many = async () => ({ ...snapshot, changes: Array.from({ length: 13 }, (_, i) => ({ ...change, id: String(i) })) })
  render(<LawCalendarPage loader={many} initialDate="2026-11-05" />)
  await screen.findByText('예시 / 실제 법령 아님')
  expect(screen.getAllByText('예시 변경 요약')).toHaveLength(8)
  await userEvent.click(screen.getByRole('button', { name: /입법예고 더 보기/ }))
  expect(screen.getAllByText('예시 변경 요약')).toHaveLength(13)
})
