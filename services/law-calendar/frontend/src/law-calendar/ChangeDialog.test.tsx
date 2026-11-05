import { render, screen, within } from '@testing-library/react'
import { ChangeDialog, highlightedText } from './ChangeDialog'
import type { CalendarChange } from './types'

test('highlighting preserves complete source text, newlines and Unicode', () => {
  const before = '제1조\n① 신청은 서면으로 한다. 📝\n② 기록한다.'
  const after = '제1조\n① 신청은 서면 또는 전자문서로 한다. 📝\n② 기록한다.'
  const { container } = render(<div>{highlightedText(after, before, 'ins')}</div>)
  expect(container.textContent).toBe(after)
  expect(container.querySelector('ins')).toHaveTextContent('또는 전자문서')
})

test('literal markup in source text cannot inject HTML', () => {
  const text = '<script>alert(1)</script>\n신설'
  const { container } = render(<div>{highlightedText(text, '', 'ins')}</div>)
  expect(container.textContent).toBe(text)
  expect(container.querySelector('script')).toBeNull()
})

const storedChange: CalendarChange = {
  id: 'effective', kind: 'effective', date: '2026-12-08', lawName: '[예시] 가상법', article: '제2조',
  summary: '제2조 삭제', status: '시행일 자동 확인 · 비교 미확인', before: '##### 제2조\n\n종전 원문\n',
  after: '##### 제2조\n\n삭제 <2026.4.7>\n', comparable: false, publishedOn: null, promulgatedOn: '2026-04-07',
  effectiveOn: '2026-12-08', commentDeadline: null, sourceUrl: null, collectedAt: null, verifiedAt: null,
}

const dialogPrototype = HTMLDialogElement.prototype
const originalShow = Object.getOwnPropertyDescriptor(dialogPrototype, 'showModal')
const originalClose = Object.getOwnPropertyDescriptor(dialogPrototype, 'close')

beforeAll(() => {
  Object.defineProperty(dialogPrototype, 'showModal', { configurable: true,
    value(this: HTMLDialogElement) { this.open = true } })
  Object.defineProperty(dialogPrototype, 'close', { configurable: true,
    value(this: HTMLDialogElement) { this.open = false } })
})
afterAll(() => {
  if (originalShow) Object.defineProperty(dialogPrototype, 'showModal', originalShow)
  else Reflect.deleteProperty(dialogPrototype, 'showModal')
  if (originalClose) Object.defineProperty(dialogPrototype, 'close', originalClose)
  else Reflect.deleteProperty(dialogPrototype, 'close')
})

function renderChange(change: CalendarChange) {
  return render(<ChangeDialog change={change} demo={false} onClose={vi.fn()} />)
}

test('shows both stored originals with highlighting and separate verification status', () => {
  renderChange(storedChange)
  const comparison = screen.getByRole('group', { name: '저장 원문 비교' })
  expect(screen.getByText('시행 전후 효력 미검증')).toBeVisible()
  expect(within(comparison).getByRole('heading', { name: 'Before · 저장 구판' })).toBeVisible()
  expect(within(comparison).getByRole('heading', { name: 'After · 저장 신판' })).toBeVisible()
  const [before, after] = comparison.querySelectorAll('pre')
  expect(before.textContent).toBe(storedChange.before)
  expect(after.textContent).toBe(storedChange.after)
  expect(after.querySelector('ins')?.textContent).toBe('삭제 <2026.4.7>')
  expect(screen.queryByRole('heading', { name: /시행 직전|시행 이후/ })).not.toBeInTheDocument()
})

test('shows an available stored original without inferring a repeal or highlighting an absent side', () => {
  renderChange({ ...storedChange, after: null })
  const comparison = screen.getByRole('group', { name: '저장 원문 비교' })
  expect(within(comparison).getByText('저장 신판 없음')).toBeVisible()
  expect(comparison.querySelector('pre')?.textContent).toBe(storedChange.before)
  expect(comparison.querySelector('del')).toBeNull()
  expect(within(comparison).queryByText(/삭제/)).not.toBeInTheDocument()
})
