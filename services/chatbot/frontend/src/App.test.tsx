import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

function sse(answer: string, title: string) {
  const ev = (e: string, d: unknown) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`
  return (
    ev('sources', { groups: [{ label: null, open: true, items: [item(title)] }] }) +
    ev('done', { text: answer, finish: 'stop', turn_id: null })
  )
}

/** Fake lawbot-web: login state + one queued chat answer per question. */
function server({ loggedIn = true } = {}) {
  let session = loggedIn
  const answers: [string, string][] = []
  const chats: unknown[] = []
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status })
    if (url === '/api/login') {
      const { password } = JSON.parse(String(init!.body))
      if (password !== 'pw') return json(401, { detail: '사용자명 또는 비밀번호가 틀렸습니다.' })
      session = true
      return json(200, { user: 'u' })
    }
    if (!session) return json(401, { detail: '로그인이 필요합니다.' })
    if (url === '/api/me') return json(200, { user: 'u' })
    if (url === '/api/config') return json(200, config)
    if (url === '/api/chat') {
      chats.push(JSON.parse(String(init!.body)))
      const [a, t] = answers.shift()!
      return new Response(sse(a, t), { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    return json(404, {})
  })
  vi.stubGlobal('fetch', fetch)
  return { answers, chats }
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
