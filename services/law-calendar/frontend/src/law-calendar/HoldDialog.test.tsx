import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HoldDialog } from './HoldDialog'
import type { HoldLoader } from './types'

const originalShow = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close')
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true,
    value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true,
    value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
afterAll(() => {
  if (originalShow) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', originalShow)
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
  if (originalClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose)
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
})

const item = { id: 'held', lawName: '[예시] 가상법', article: '제1조', reason: '대통령령으로 정하는 시행일 확인 필요',
  sourceUrl: 'https://www.law.go.kr/법령/예시', collectedAt: '2026-11-05T10:00:00+09:00' }

test('shows undated holds, pages, and no editing controls', async () => {
  const loader: HoldLoader = vi.fn(async (page) => ({ items: [{ ...item, article: `제${page}조` }], total: 21, page, page_size: 20 }))
  const onClose = vi.fn()
  render(<HoldDialog demo={false} loader={loader} onClose={onClose} />)
  expect(await screen.findByText('제1조')).toBeInTheDocument()
  expect(screen.getByText(/대통령령으로/)).toBeInTheDocument()
  expect(screen.getByText(/달력의 날짜별 건수에 포함하지/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /공식 원문/ })).toHaveAttribute('href', item.sourceUrl)
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /승인|수정|확정/ })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '다음 보류 목록' }))
  expect(await screen.findByText('제2조')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '보류 목록 닫기' }))
  expect(onClose).toHaveBeenCalledOnce()
})

test('handles empty results and retries failures without showing internal errors', async () => {
  const loader: HoldLoader = vi.fn().mockRejectedValueOnce(new Error('private internal error'))
    .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  render(<HoldDialog demo={false} loader={loader} onClose={() => {}} />)
  expect(screen.getByRole('status')).toHaveTextContent('불러오는 중')
  expect(await screen.findByRole('alert')).toHaveTextContent('불러오지 못했습니다')
  expect(screen.queryByText(/private internal/)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '다시 시도' }))
  expect(await screen.findByText('현재 공개된 보류 항목이 없습니다.')).toBeInTheDocument()
})
