import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { Config } from './types'

const config: Config = {
  title: 'Lawbot · 테스트',
  intro: '안내',
  notice: '법령 기준일 2026-10-20',
  models: [
    { key: 'pro', label: 'Lawbot Pro', info: '27B', format: 'concise' },
    { key: 'fast', label: 'Lawbot Fast', info: '4B', format: 'full' },
  ],
  disabled: [],
  default_model: 'pro',
  formats: [
    { value: 'concise', label: '간결' },
    { value: 'full', label: '전체 (4항목)' },
  ],
  examples: ['예시 질문'],
  sources_title: '검색된 조문',
  feedback: { enabled: false, reasons: [] },
}

const item = (title: string) => ({ title, scores: 's', body: `${title} 본문`, decree: false })
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status })

function sse(answer: string, title: string) {
  const ev = (e: string, d: unknown) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`
  return (
    ev('sources', { groups: [{ label: null, open: true, items: [item(title)] }] }) +
    ev('done', { text: answer, finish: 'stop', turn_id: null })
  )
}

/** Fake lawbot-web: login state + one queued chat answer per question. */
function server({ loggedIn = true, role, adminRoutes }: {
  loggedIn?: boolean; role?: 'user' | 'admin'; adminRoutes?: (url: URL, init?: RequestInit) => Response | Promise<Response>
} = {}) {
  let session = loggedIn
  const answers: [string, string][] = []
  const chats: unknown[] = []
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/login') {
      const { password } = JSON.parse(String(init!.body))
      if (password !== 'pw') return json(401, { detail: '사용자명 또는 비밀번호가 틀렸습니다.' })
      session = true
      return json(200, { user: 'u' })
    }
    if (!session) return json(401, { detail: '로그인이 필요합니다.' })
    if (url === '/api/me') return json(200, { user: 'u', role })
    if (url === '/api/config') return json(200, config)
    if (url.startsWith('/api/admin/')) {
      const path = new URL(url, 'http://testserver')
      return adminRoutes?.(path, init) ?? json(503, { code: 'calendar_not_configured', detail: '미설정' })
    }
    if (url === '/api/chat') {
      chats.push(JSON.parse(String(init!.body)))
      const [a, t] = answers.shift()!
      return new Response(sse(a, t), { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    return json(404, {})
  })
  vi.stubGlobal('fetch', fetch)
  return { answers, chats, fetch }
}

afterEach(() => vi.unstubAllGlobals())

async function ask(q: string) {
  const box = await screen.findByRole('textbox', { name: '질문' })
  await userEvent.type(box, q)
  await userEvent.keyboard('{Enter}')
}

test('login: wrong password shows the error, right one opens the chat', async () => {
  server({ loggedIn: false })
  render(<App />)
  await userEvent.type(await screen.findByLabelText('사용자명'), 'u')
  await userEvent.type(screen.getByLabelText('비밀번호'), 'bad')
  await userEvent.click(screen.getByRole('button', { name: '로그인' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('틀렸습니다')
  await userEvent.clear(screen.getByLabelText('비밀번호'))
  await userEvent.type(screen.getByLabelText('비밀번호'), 'pw')
  await userEvent.click(screen.getByRole('button', { name: '로그인' }))
  expect(await screen.findByRole('heading', { name: config.title })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'Lawbot Pro' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: '간결' })).toHaveAttribute('aria-checked', 'true')
})

test('a session with an admin role still opens the chat', async () => {
  server({ role: 'admin' })
  render(<App />)
  expect(await screen.findByRole('textbox', { name: '질문' })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: '채팅' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: '피드백 & 법령 관리' })).toBeVisible()
})

test.each([undefined, 'user'] as const)('role %s has no management switch', async (role) => {
  server({ role })
  render(<App />)
  expect(await screen.findByRole('textbox', { name: '질문' })).toBeVisible()
  expect(screen.queryByRole('radio', { name: '피드백 & 법령 관리' })).not.toBeInTheDocument()
})

test('choosing a model switches the format to its default; the request carries both', async () => {
  const s = server()
  s.answers.push(['답', '근로기준법 제26조'])
  render(<App />)
  await userEvent.click(await screen.findByRole('radio', { name: 'Lawbot Fast' }))
  expect(screen.getByRole('radio', { name: '전체 (4항목)' })).toHaveAttribute('aria-checked', 'true')
  await ask('질문')
  await screen.findByText('답')
  expect(s.chats).toEqual([{ question: '질문', model: 'fast', format: 'full' }])
})

test('clicking an earlier answer shows its articles; a new question follows the latest again', async () => {
  const s = server()
  s.answers.push(['첫 답', '첫 조문'], ['둘째 답', '둘째 조문'], ['셋째 답', '셋째 조문'])
  render(<App />)
  const panel = () => within(screen.getByTestId('lawbot-sources'))
  await ask('하나')
  await screen.findByText('첫 답')
  await ask('둘')
  await screen.findByText('둘째 답')
  expect(panel().getByText('둘째 조문')).toBeInTheDocument()
  await userEvent.click(screen.getByText('첫 답'))
  expect(panel().getByText('첫 조문')).toBeInTheDocument()
  await ask('셋')
  await screen.findByText('셋째 답')
  expect(panel().getByText('셋째 조문')).toBeInTheDocument()
})

test('Enter sends, Shift+Enter and Korean IME composition do not', async () => {
  const s = server()
  s.answers.push(['답', '조문'])
  render(<App />)
  const box = await screen.findByRole('textbox', { name: '질문' })
  await userEvent.type(box, '첫줄{Shift>}{Enter}{/Shift}둘째줄')
  fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
  expect(s.chats).toEqual([])
  await userEvent.keyboard('{Enter}')
  await waitFor(() => expect(s.chats).toEqual([{ question: '첫줄\n둘째줄', model: 'pro', format: 'concise' }]))
  expect(box).toHaveValue('')
})

test('example question is sent on click', async () => {
  const s = server()
  s.answers.push(['예시 답', '조문'])
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '예시 질문' }))
  expect(await screen.findByText('예시 답')).toBeInTheDocument()
  expect(s.chats).toEqual([{ question: '예시 질문', model: 'pro', format: 'concise' }])
})

test('scope wording: Lawbot on login, HR·attendance·payroll law on the empty chat', async () => {
  server({ loggedIn: false })
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Lawbot' })).toBeInTheDocument()
  vi.unstubAllGlobals()
  server()
  render(<App />)
  expect(await screen.findByRole('heading', { name: '인사·근태·급여 관련 법, 조문을 근거로 답해 드려요' })).toBeInTheDocument()
  expect(screen.getAllByPlaceholderText('인사·근태·급여 관련 법을 질문하세요').length).toBeGreaterThan(0)
})

async function management() {
  render(<App />)
  await userEvent.click(await screen.findByRole('radio', { name: '피드백 & 법령 관리' }))
}

function row(id: string, label = 'like', status = 'pending', warnings: string[] = []) {
  return {
    feedback_id: id, turn_id: id, ts: '2026-11-08T00:00:00+00:00', label, status, warnings,
    question_preview: id === 'flag-1' ? '신고 질문' : `질문 ${id}`, reason: label === 'flag' ? 'answer' : null,
    latest_review: null,
  }
}

const page = (items: unknown[] = [], next_cursor: string | null = null) => json(200, { items, next_cursor })
const unavailable = () => json(503, { code: 'calendar_not_configured', detail: '미설정' })
const gold = { law: '근로기준법', article_no: '제26조' }
const detail = {
  feedback: {
    feedback_id: 'flag-1', question: '원래 질문', answer_raw: '기존 원문', answer_shown: '화면 답변',
    hits: [{ law: gold.law, article_id: gold.article_no, doc: '이전 조문' }], citations: [],
  },
  reviews: [], promotions: [],
  checks: { overlap: null, corpus: { changed_hits: [{ law: gold.law, article_id: gold.article_no, status: 'changed' }] } },
}

test('feedback loads independently of an unconfigured calendar and can be empty', async () => {
  let finish!: (response: Response) => void
  server({
    role: 'admin',
    adminRoutes: (url) => url.pathname.includes('/calendar/') ? unavailable()
      : url.searchParams.get('label') === 'like' ? new Promise<Response>((resolve) => { finish = resolve }) : page(),
  })
  await management()
  const likes = within(screen.getByRole('region', { name: '좋아요' }))
  expect(likes.getByRole('status')).toHaveTextContent('불러오는 중')
  expect(await screen.findByText('캘린더 연결 안 됨')).toBeVisible()
  finish(page())
  expect(await likes.findByText('표시할 피드백이 없습니다.')).toBeVisible()
})

test.each([[401, 'login_required'], [403, 'admin_required'], [403, undefined]] as const)(
  'feedback response %s (%s) shows the correct session state', async (status, code) => {
    server({
      role: 'admin', adminRoutes: (url) => url.pathname.includes('/calendar/')
        ? unavailable() : json(status, { code, detail: '서버 안내' }),
    })
    await management()
    if (status === 401) expect(await screen.findByLabelText('비밀번호')).toBeVisible()
    else {
      const likes = within(screen.getByRole('region', { name: '좋아요' }))
      expect(await likes.findByRole('alert')).toHaveTextContent('관리자 권한')
    }
  },
)

test('feedback filters and pagination show the server warning badges', async () => {
  const s = server({
    role: 'admin', adminRoutes: (url) => {
      if (url.pathname.includes('/calendar/')) return unavailable()
      if (url.searchParams.get('label') !== 'flag') return page()
      if (url.searchParams.has('cursor')) return page([row('tail', 'flag')])
      return page([row('flag-1', 'flag', 'quarantined', ['leak_test', 'label_changed', 'corpus_changed'])], 'cursor+2')
    },
  })
  await management()
  const flags = within(screen.getByRole('region', { name: '문제 신고' }))
  expect(await flags.findByText('라벨 바뀜')).toBeVisible()
  expect(flags.getByText('조문 버전 다름')).toBeVisible()
  expect(within(flags.getByRole('list', { name: '피드백 목록' })).getByText('격리')).toBeVisible()
  await userEvent.selectOptions(flags.getByLabelText('상태'), 'all')
  await userEvent.selectOptions(flags.getByLabelText('신고 사유'), 'answer')
  await userEvent.type(flags.getByRole('searchbox', { name: '질문 검색' }), '신고')
  await userEvent.click(flags.getByRole('button', { name: '검색' }))
  await userEvent.click(await flags.findByRole('button', { name: '더 보기' }))
  expect(await flags.findByRole('button', { name: '질문 tail' })).toBeVisible()
  const last = new URL(s.fetch.mock.calls.filter(([url]) => url.includes('cursor=')).at(-1)![0], 'http://testserver')
  expect(Object.fromEntries(last.searchParams)).toMatchObject({ label: 'flag', status: 'all', reason: 'answer', q: '신고', cursor: 'cursor+2' })
})

test('batch promotion displays each success and failure', async () => {
  const s = server({
    role: 'admin', adminRoutes: (url) => {
      if (url.pathname.includes('/calendar/')) return unavailable()
      if (url.pathname.endsWith('promote-up')) return json(200, { results: [
        { feedback_id: 'one', ok: true, qa_uid: 'feedback:FB-one-r1' },
        { feedback_id: 'two', ok: false, code: 'not_reviewed', detail: '먼저 검수해야 합니다.' },
      ] })
      return url.searchParams.get('label') === 'like' ? page([row('one'), row('two')]) : page()
    },
  })
  await management()
  const likes = within(screen.getByRole('region', { name: '좋아요' }))
  await userEvent.click(await likes.findByRole('checkbox', { name: '질문 one 선택' }))
  await userEvent.click(likes.getByRole('checkbox', { name: '질문 two 선택' }))
  await userEvent.click(likes.getByRole('button', { name: '선택 승격 (2)' }))
  expect(await likes.findByText(/질문 one.*승격 완료/)).toBeVisible()
  expect(likes.getByText(/질문 two.*먼저 검수/)).toBeVisible()
  const [, init] = s.fetch.mock.calls.find(([url]) => url.endsWith('promote-up'))!
  expect(JSON.parse(String(init?.body))).toEqual({ feedback_ids: ['one', 'two'], idempotency_key: expect.any(String) })
  expect(init?.headers).toEqual({ 'content-type': 'application/json' })
})

function stubDialog() {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value(this: HTMLDialogElement) { this.open = true } },
    close: { configurable: true, value(this: HTMLDialogElement) { this.open = false } },
  })
}

function correctionServer(post: () => Response | Promise<Response> = () => json(201, { promotion_id: 1 }), status = 'pending') {
  stubDialog()
  return server({
    role: 'admin', adminRoutes: (url) => {
      if (url.pathname.includes('/calendar/')) return unavailable()
      if (url.pathname.endsWith('promote-down')) return post()
      if (url.pathname.endsWith('/articles')) return json(200, { items: [gold] })
      if (url.pathname.endsWith('/flag-1')) return json(200, detail)
      return url.searchParams.get('label') === 'flag' ? page([row('flag-1', 'flag', status)]) : page()
    },
  })
}

async function correction() {
  await management()
  const opener = await screen.findByRole('button', { name: '신고 질문' })
  await userEvent.click(opener)
  return { opener, dialog: await screen.findByRole('dialog', { name: '문제 신고 교정' }) }
}

async function completeCorrection(dialog: HTMLElement) {
  const view = within(dialog)
  await userEvent.type(view.getByLabelText('교정 질문'), '고친 질문')
  await userEvent.click(view.getByRole('button', { name: '기존 답변을 초안으로 복사' }))
  expect(view.getByLabelText('교정 답변')).toHaveValue('기존 원문')
  expect(view.getByRole('radio', { name: '정답' })).not.toBeChecked()
  expect(view.getByRole('radio', { name: '오답' })).not.toBeChecked()
  await userEvent.click(view.getByRole('radio', { name: '오답' }))
  await userEvent.type(view.getByLabelText('근거 조문 검색'), '제26조')
  await userEvent.click(await view.findByRole('button', { name: '근로기준법 제26조' }))
}

test('correction dialog labels, first focus, tab order and Escape restore focus', async () => {
  correctionServer()
  const { dialog, opener } = await correction()
  const view = within(dialog)
  expect(view.getByLabelText('원 질문')).toHaveAttribute('readonly')
  expect(view.getByLabelText('원 질문')).toHaveValue('원래 질문')
  expect(view.getByText('화면 답변', { exact: true })).toBeVisible()
  expect(view.getByText(/근로기준법 제26조.*변경/)).toBeVisible()
  expect(view.getByLabelText('교정 질문')).toHaveFocus()
  await userEvent.tab()
  expect(view.getByLabelText('교정 답변')).toHaveFocus()
  expect(view.getByRole('button', { name: '승격' })).toBeDisabled()
  expect(view.getByText(/교정 질문.*교정 답변.*근거 조문.*판정/)).toBeVisible()
  fireEvent(dialog, new Event('cancel', { cancelable: true }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
})

test('a quarantined correction cannot promote even when all fields are present', async () => {
  correctionServer(undefined, 'quarantined')
  const { dialog } = await correction()
  await completeCorrection(dialog)
  expect(within(dialog).getByRole('button', { name: '승격' })).toBeDisabled()
  expect(within(dialog).getByText(/격리된 피드백은 승격할 수 없습니다/)).toBeVisible()
})

test.each([
  ['leak_test', 409, /시험 데이터/], ['leak_val', 409, /검증 데이터/], ['leak_eval', 409, /평가 전용/],
  ['unknown_article', 422, /근거 조문/],
  ['bad_origin', 403, /요청 출처를 확인할 수 없어 저장하지 않았습니다\. 같은 주소에서 다시 열어 주세요\./],
] as const)('correction reports %s', async (code, status, message) => {
  correctionServer(() => json(status, { code, detail: '서버 오류 설명' }))
  const { dialog } = await correction()
  await completeCorrection(dialog)
  await userEvent.click(within(dialog).getByRole('button', { name: '승격' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(message)
})

test('network retry reuses the key and editing replaces it; saving disables actions', async () => {
  let reject!: (reason: unknown) => void
  const post = vi.fn(() => new Promise<Response>((_resolve, fail) => { reject = fail }))
  const s = correctionServer(post)
  const { dialog } = await correction()
  const view = within(dialog)
  await completeCorrection(dialog)
  const promote = view.getByRole('button', { name: '승격' })
  await userEvent.click(promote)
  expect(promote).toBeDisabled()
  expect(view.getByRole('button', { name: '보류' })).toBeDisabled()
  reject(new TypeError('internal network info'))
  expect(await view.findByRole('alert')).toHaveTextContent('서버에 연결하지 못했습니다')
  await userEvent.click(promote)
  reject(new TypeError('internal network info'))
  await waitFor(() => expect(promote).toBeEnabled())
  await userEvent.type(view.getByLabelText('검수 메모'), '변경')
  await userEvent.click(promote)
  reject(new TypeError('internal network info'))
  await waitFor(() => expect(promote).toBeEnabled())
  const bodies = s.fetch.mock.calls.filter(([url]) => url.endsWith('promote-down')).map(([, init]) => JSON.parse(String(init?.body)))
  expect(bodies).toHaveLength(3)
  expect(bodies[0]).toMatchObject({ corrected_question: '고친 질문', corrected_answer: '기존 원문', gold_articles: [gold], review_verdict: 'incorrect' })
  expect(bodies[1].idempotency_key).toBe(bodies[0].idempotency_key)
  expect(bodies[2].idempotency_key).not.toBe(bodies[0].idempotency_key)
})

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
})

test('calendar events, details, comparison images and holds use the admin proxy', async () => {
  stubDialog()
  const date = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date())
  const change = {
    id: 'cal-id', kind: 'notice', date, lawName: '합성 법령', article: '제1조', summary: '예고 요약',
    status: '공개', comparable: false, before: null, after: null,
    noticeContent: { summary: '주요 내용', pdfUrl: null, comparisonPages: [2] },
  }
  const s = server({
    role: 'admin', adminRoutes: (url) => {
      if (url.pathname.endsWith('/law-calendar')) return json(200, { days: [] })
      if (url.pathname.endsWith('/law-calendar/status')) return json(200, { collectedAt: '합성 수집 시각', delayed: false })
      if (url.pathname.endsWith('/law-events')) return json(200, { items: [change], page: 1, total: 1 })
      if (url.pathname.endsWith('/law-changes/cal-id')) return json(200, change)
      if (url.pathname.endsWith('/law-holds')) return json(200, {
        items: [], total: 0, page: Number(url.searchParams.get('page')), page_size: 20,
      })
      return page()
    },
  })
  await management()
  const calendar = within(screen.getByRole('region', { name: '법령 캘린더' }))
  expect(await calendar.findByText(/합성 수집 시각/)).toBeVisible()
  await userEvent.click(await calendar.findByRole('button', { name: /합성 법령 제1조/ }))
  const comparison = within(await screen.findByRole('dialog', { name: '합성 법령 제1조' }))
  await userEvent.click(comparison.getByText('공식 신·구조문대비표'))
  expect(comparison.getByRole('img', { name: '공식 신·구조문대비표 원문 2쪽' }))
    .toHaveAttribute('src', '/api/admin/calendar/law-changes/cal-id/notice-comparison/2')
  await userEvent.click(comparison.getByRole('button', { name: '변경 상세 닫기' }))
  await userEvent.click(calendar.getByRole('button', { name: '보류 목록' }))
  const holds = within(await screen.findByRole('dialog', { name: '보류 목록' }))
  await userEvent.click(holds.getByRole('button', { name: '입법예고 검토 대기' }))
  expect(await holds.findByText('현재 공개된 보류 항목이 없습니다.')).toBeVisible()
  const paths = s.fetch.mock.calls.map(([url]) => url)
  expect(paths).toContain('/api/admin/calendar/law-calendar/status')
  expect(paths).toContain('/api/admin/calendar/law-changes/cal-id')
  expect(paths).toContain('/api/admin/calendar/law-holds?type=notice&page=1&page_size=20')
  expect(paths.some((url) => url.startsWith('/api/law-'))).toBe(false)
})

test('approving a notice with only an end date opens its calendar month and refreshes holds', async () => {
  stubDialog()
  const notice = { id: 'notice-id', lawName: '합성 개정안', billNo: '1234', reason: '기간 확인', sourceUrl: 'https://example.test/notice' }
  let reviewed = false
  const s = server({
    role: 'admin', adminRoutes: (url, init) => {
      const currentPage = Number(url.searchParams.get('page') ?? '1')
      if (url.pathname.endsWith('/law-calendar')) return json(200, { days: [] })
      if (url.pathname.endsWith('/law-calendar/status')) return json(200, { delayed: false })
      if (url.pathname.endsWith('/law-events')) return json(200, { items: [], page: 1, total: 0 })
      if (url.pathname.endsWith('/law-holds')) {
        const items = url.searchParams.get('type') === 'notice' && !reviewed ? [notice] : []
        return json(200, { items, page: currentPage, total: items.length, page_size: 20 })
      }
      if (url.pathname.endsWith('/law-holds/notice/notice-id')) {
        if (init?.method === 'POST') {
          reviewed = true
          return json(200, { ok: true })
        }
        return json(200, {
          template: { snapshot_id: notice.id, decision: 'approve', note: '' }, title: notice.lawName,
          source_url: notice.sourceUrl, bill_no: notice.billNo, dates: [{ role: 'notice_end', date: '2030-05-15' }],
        })
      }
      if (url.pathname.endsWith('/import-failures')) return json(200, {
        items: [{ source: 'assembly_notice', event_id: 'notice-bad', error_type: 'ValueError', message: '예고일 누락', occurred_at: '2026-11-10T00:00:00+00:00' }],
        page: currentPage, total: 1, page_size: 20,
      })
      return page()
    },
  })
  await management()
  const calendar = within(screen.getByRole('region', { name: '법령 캘린더' }))
  await userEvent.click(await calendar.findByRole('button', { name: '보류 목록' }))
  const holds = within(screen.getByRole('dialog', { name: '보류 목록' }))
  await userEvent.click(holds.getByRole('button', { name: '입법예고 검토 대기' }))
  await userEvent.click(await holds.findByRole('button', { name: /검토 열기.*합성 개정안/ }))
  expect(await holds.findByText('예고 종료일')).toBeVisible()
  expect(holds.getByLabelText('검토 결정')).toHaveValue('approve')
  await userEvent.type(holds.getByLabelText('검토 메모'), '공식 예고 종료일을 확인했습니다.')
  await userEvent.click(holds.getByRole('button', { name: /^승인$/ }))
  expect(await holds.findByText('검토 결정을 저장했습니다.')).toBeVisible()
  expect(await holds.findByText('현재 공개된 보류 항목이 없습니다.')).toBeVisible()
  expect(s.fetch.mock.calls.filter(([url]) => url.startsWith('/api/admin/calendar/law-events?')).at(-1)?.[0])
    .toContain('month=2030-05')
  const [, decision] = s.fetch.mock.calls.find(([, init]) => init?.method === 'POST')!
  expect(JSON.parse(String(decision?.body))).toEqual({
    snapshot_id: notice.id, decision: 'approve', note: '공식 예고 종료일을 확인했습니다.', idempotency_key: expect.any(String),
  })
  await userEvent.click(holds.getByRole('button', { name: '보류 목록 닫기' }))
  await userEvent.click(calendar.getByRole('button', { name: '가져오기 실패' }))
  const failures = within(await screen.findByRole('dialog', { name: '가져오기 실패' }))
  expect(await failures.findByText('예고일 누락')).toBeVisible()
  expect(failures.getByText('notice-bad')).toBeVisible()
  expect(s.fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
