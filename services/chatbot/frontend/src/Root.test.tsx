import { render, screen } from '@testing-library/react'
import { Root } from './Root'

afterEach(() => {
  window.history.replaceState({}, '', '/')
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

test('calendar direct links bypass chatbot session requests, even with a query string', async () => {
  vi.stubEnv('DEV', false)
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  window.history.replaceState({}, '', '/law-calendar/?demo=true')
  render(<Root />)
  expect(await screen.findByRole('heading', { name: '법령 변경 캘린더', level: 1 })).toBeInTheDocument()
  expect(await screen.findByText(/승인 데이터 연결 전/)).toBeInTheDocument()
  expect(fetch).not.toHaveBeenCalled()
})

test('chatbot root still requires login', async () => {
  const fetch = vi.fn(async () => new Response('{}', { status: 401 }))
  vi.stubGlobal('fetch', fetch)
  render(<Root />)
  expect(await screen.findByLabelText('비밀번호')).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/api/me', undefined)
})
