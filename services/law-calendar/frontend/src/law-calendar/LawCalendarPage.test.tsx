import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CalendarChange } from '@lawbot/ui'
import LawCalendarPage from './LawCalendarPage'
import type { CalendarLoader, CalendarSnapshot } from './types'

const change: CalendarChange = {
  id: 'effective-0', date: '2026-11-05', lawName: '[예시] 가상법', article: '제1조',
  summary: '예시 변경 요약', status: '시행 예시', before: '신청은 서면으로 한다.',
  after: '신청은 전자문서로 한다.', comparable: true,
  promulgatedOn: null, effectiveOn: '2026-11-05', sourceUrl: null,
  collectedAt: null, verifiedAt: null,
}
const snapshot: CalendarSnapshot = {
  changes: [change, { ...change, id: 'effective', summary: '예시 시행 요약', status: '시행 예시' }],
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

test('footer credits the statute repository and its metadata license', async () => {
  render(<LawCalendarPage loader={loader} initialDate="2026-11-05" />)
  const sources = await screen.findByRole('navigation', { name: '공식 출처' })
  expect(within(sources).getByRole('link', { name: /legalize-kr/ })).toHaveAttribute(
    'href', 'https://github.com/legalize-kr/legalize-kr',
  )
  expect(within(sources).getByRole('link', { name: /admrule-kr/ })).toHaveAttribute(
    'href', 'https://github.com/legalize-kr/admrule-kr',
  )
  expect(within(sources).getByText(/메타데이터 MIT/)).toBeInTheDocument()
  expect(screen.getByText('법령 정보 출처: 법제처 국가법령정보센터(www.law.go.kr)')).toBeVisible()
  expect(screen.getByRole('link', { name: '라이선스' })).toHaveAttribute('href', '/licenses.txt')
  expect(screen.getByRole('link', { name: '라이선스' }).closest('footer')).not.toBeNull()
})

test('selection updates articles, empty dates, month navigation and today', async () => {
  render(<LawCalendarPage loader={loader} initialDate="2026-11-05" />)
  expect(await screen.findByText('예시 변경 요약')).toBeInTheDocument()
  expect(screen.getByText('예시 시행 요약')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /^2026년 11월 6일/ }))
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

test('production reads only same-origin static calendar files', async () => {
  vi.stubEnv('DEV', false)
  const fetch = vi.fn(async (url: string, _init?: RequestInit) => Response.json(
    url.endsWith('months.json') ? { months: ['2026-11'], erpRuleDates: [] }
      : url.endsWith('status.json') ? { collectedAt: null, verifiedAt: null, delayed: true }
      : url.includes('tax-schedule') ? { items: [], confirmedAt: null }
      : url.includes('law-events') ? { items: [], total: 0 } : { days: [] },
  ))
  vi.stubGlobal('fetch', fetch)
  try {
    render(<LawCalendarPage initialDate="2026-11-05" />)
    expect(await screen.findByText(/이 달에 공개 승인된/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/불러오는 중/)).not.toBeInTheDocument())
    expect(fetch.mock.calls.map(([url]) => url).sort()).toEqual([
      '/data/law-calendar/2026-11.json', '/data/law-calendar/months.json', '/data/law-calendar/status.json',
      '/data/law-events/2026-11.json', '/data/tax-schedule/2026-11.json',
    ])
    expect(fetch.mock.calls.every(([, init]) => init?.credentials === 'same-origin')).toBe(true)
    expect(screen.queryByText('예시 변경 요약')).not.toBeInTheDocument()
  } finally {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  }
})

test('tax badge announces the deadline count and opens the official schedule for the selected day', async () => {
  vi.stubEnv('DEV', false)
  const source = 'https://www.nts.go.kr/nts/ad/taxSchdul/selectList.do?mi=135747&taxMonth=&taxYear=2026'
  const fetch = vi.fn(async (url: string) => Response.json(
    url.endsWith('months.json') ? { months: ['2026-11'], erpRuleDates: [] } : url.includes('tax-schedule') ? {
      confirmedAt: '2026-11-14T06:30:00+09:00', items: [{
        id: 'deadline', title: '일용근로소득 지급명세서', date: '2026-11-30',
        targetPeriod: '2026년 10월 지급분', sourceUrl: source,
      }],
    } : url.includes('law-events') ? { items: [], total: 0, page: 1 } : { days: [] },
  ))
  vi.stubGlobal('fetch', fetch)
  try {
    render(<LawCalendarPage initialDate="2026-11-05" />)
    const day = await screen.findByRole('button', { name: /11월 30일.*세무일정 1건/ })
    expect(within(day).getByText('세무 1')).toHaveClass('lc-tax')
    await userEvent.click(day)
    const deadlines = screen.getByRole('region', { name: '세무일정 1건' })
    expect(within(deadlines).getByText(/대상 2026년 10월 지급분/)).toBeInTheDocument()
    expect(within(deadlines).getByRole('link', { name: /국세청 원문/ })).toHaveAttribute('href', source)
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
  await userEvent.click(screen.getByRole('button', { name: /시행 조문 더 보기/ }))
  expect(screen.getAllByText('예시 변경 요약')).toHaveLength(13)
})

describe('mobile date sheet', () => {
  const source = 'https://www.nts.go.kr/nts/ad/taxSchdul/selectList.do?mi=135747&taxMonth=&taxYear=2026'
  const mixed: CalendarSnapshot = {
    ...snapshot,
    changes: Array.from({ length: 17 }, (_, index) => ({ ...change, id: `effective-${index}` })),
    tax: { confirmedAt: '2026-11-14T06:30:00+09:00', items: ['2026-11-05', '2026-11-30'].map(date => ({
      id: date, title: '일용근로소득 지급명세서', date, targetPeriod: '2026년 10월 지급분', sourceUrl: source,
    })) },
  }
  const loadMixed: CalendarLoader = async () => mixed
  const day = (label: string) => within(screen.getByRole('grid')).getByRole('button', { name: new RegExp(`^${label},`) })
  const mount = (load = loadMixed) => render(<LawCalendarPage loader={load} initialDate="2026-11-06" />)
  const close = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: '날짜 내역 닫기' })
  const originalShow = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
  const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close')

  beforeAll(() => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true,
      value(this: HTMLDialogElement) {
        this.setAttribute('open', '')
        this.querySelector<HTMLElement>('button')?.focus()
      } })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true,
      value(this: HTMLDialogElement) { this.removeAttribute('open') } })
  })
  afterAll(() => {
    if (originalShow) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', originalShow)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
    if (originalClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
  })
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: media === '(max-width: 700px)' }))
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  test('shows labelled demo, article and tax content without duplicate ids, in pages of eight', async () => {
    mount()
    await screen.findByRole('button', { name: /11월 5일, 시행 17건/ })
    await userEvent.click(day('2026년 11월 5일'))
    const sheet = within(screen.getByRole('dialog', { name: '2026년 11월 5일' }))
    expect(sheet.getByText('예시 / 실제 법령 아님')).toBeVisible()
    const articles = within(sheet.getByRole('region', { name: '시행 조문 17건' }))
    const rows = () => articles.getAllByRole('button', { name: /\[예시\] 가상법 제1조.*예시 변경 요약.*시행 예시/ })
    expect(rows()).toHaveLength(8)
    await userEvent.click(articles.getByRole('button', { name: /더 보기/ }))
    expect(rows()).toHaveLength(16)
    await userEvent.click(articles.getByRole('button', { name: /더 보기/ }))
    expect(rows()).toHaveLength(17)
    const tax = within(sheet.getByRole('region', { name: '세무일정 1건' }))
    expect(tax.getByText('일용근로소득 지급명세서')).toBeVisible()
    expect(tax.getByText('기한 2026년 11월 5일 · 대상 2026년 10월 지급분')).toBeVisible()
    const link = tax.getByRole('link', { name: '국세청 원문 ↗' })
    expect(link).toHaveAttribute('href', source)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    const ids = Array.from(document.querySelectorAll('[id]'), element => element.id)
    expect(ids).toEqual([...new Set(ids)])
    await userEvent.click(sheet.getByRole('button', { name: '날짜 내역 닫기' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  test('opens a tax-only date', async () => {
    mount()
    const date = await screen.findByRole('button', { name: /11월 30일, 시행 0건, 세무일정 1건/ })
    await userEvent.click(date)
    const sheet = screen.getByRole('dialog', { name: '2026년 11월 30일' })
    expect(within(sheet).getByRole('link', { name: '국세청 원문 ↗' })).toBeVisible()
    await userEvent.click(close(sheet))
    await waitFor(() => expect(date).toHaveFocus())
  })

  test('does not open for empty days, keyboard navigation, controls or dates in another month', async () => {
    const load: CalendarLoader = async (month) => month === '2026-12'
      ? { ...snapshot, changes: [{ ...change, date: '2026-12-01' }] } : mixed
    mount(load)
    await screen.findByRole('button', { name: /11월 5일, 시행 17건/ })
    await userEvent.click(day('2026년 11월 6일'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.keyboard('{ArrowLeft}')
    expect(day('2026년 11월 5일')).toHaveFocus()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    for (const key of ['Home', 'End', 'PageUp', 'PageDown']) {
      await userEvent.keyboard(`{${key}}`)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    }
    await userEvent.click(day('2026년 12월 1일'))
    expect(await screen.findByRole('button', { name: /12월 1일, 시행 1건/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    for (const name of ['이전 달', '다음 달', '오늘']) {
      await userEvent.click(screen.getByRole('button', { name }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    }
    expect(screen.getByRole('button', { current: 'date' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('does not open while loading or after a load error', async () => {
    let fail: () => void = () => { throw new Error('Loader did not start') }
    mount(() => new Promise((_, reject) => { fail = () => reject(new Error('Fixture unavailable')) }))
    await userEvent.click(day('2026년 11월 5일'))
    expect(screen.getByRole('status')).toHaveTextContent('불러오는 중')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fail()
    expect(await screen.findByRole('alert')).toHaveTextContent('불러오지 못했습니다')
    await userEvent.click(day('2026년 11월 5일'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('keeps desktop selection and the inline list', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    mount()
    await screen.findByRole('button', { name: /11월 5일, 시행 17건/ })
    await userEvent.click(day('2026년 11월 5일'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '시행 조문 17건' })).toBeVisible()
  })

  test('closes detail via Escape and the sheet via its button through history', async () => {
    const back = vi.spyOn(history, 'back')
    mount()
    const date = await screen.findByRole('button', { name: /11월 5일, 시행 17건/ })
    await userEvent.click(date)
    const sheet = screen.getByRole('dialog', { name: '2026년 11월 5일' })
    const item = within(sheet).getAllByRole('button', { name: /예시 변경 요약/ })[0]
    await userEvent.click(item)
    const detailDialog = screen.getByRole('button', { name: '상세 닫기' }).closest('dialog')!
    const detail = within(detailDialog)
    const comparison = detail.getByRole('group', { name: '검증된 원문 비교' })
    expect(comparison).toBeVisible()
    expect(comparison).toHaveTextContent('신청은 전자문서로 한다.')
    fireEvent(detailDialog, new Event('cancel', { cancelable: true }))
    expect(back).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('button', { name: '상세 닫기' })).not.toBeInTheDocument())
    expect(sheet).toBeVisible()
    await userEvent.click(close(sheet))
    expect(back).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(sheet).not.toBeInTheDocument())
    expect(date).toHaveFocus()
  })

  test('shows a failed public detail load inside the open sheet', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 404 })))
    mount(async () => ({ ...mixed, demo: false }))
    await userEvent.click(await screen.findByRole('button', { name: /11월 5일, 시행 17건/ }))
    const sheet = within(screen.getByRole('dialog', { name: '2026년 11월 5일' }))
    await userEvent.click(sheet.getAllByRole('button', { name: /예시 변경 요약/ })[0])
    expect(await sheet.findByRole('alert')).toHaveTextContent('상세 정보를 확인하지 못했습니다')
    expect(screen.queryByRole('button', { name: '상세 닫기' })).not.toBeInTheDocument()
    await userEvent.click(sheet.getByRole('button', { name: '날짜 내역 닫기' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
