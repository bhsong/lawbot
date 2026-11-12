import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

afterEach(() => window.history.replaceState(null, '', '/'))

test('tabs switch between the law calendar and ERP items and follow the #erp hash', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ state: 'missing' })))
  vi.stubGlobal('fetch', fetch)
  try {
    window.history.replaceState(null, '', '/#erp')
    render(<App />)
    expect(screen.getByRole('tab', { name: 'ERP 자료' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('ERP 자료가 아직 준비되지 않았습니다.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: '법령 캘린더' }))
    expect(screen.getByRole('heading', { level: 1, name: '법령 변경 캘린더' })).toBeInTheDocument()
    expect(window.location.hash).toBe('')
    await userEvent.click(screen.getByRole('tab', { name: 'ERP 자료' }))
    expect(window.location.hash).toBe('#erp')
  } finally {
    vi.unstubAllGlobals()
  }
})
