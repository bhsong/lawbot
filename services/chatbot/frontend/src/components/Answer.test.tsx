import { render, screen } from '@testing-library/react'
import { TurnView } from './TurnView'
import type { Turn } from '../types'

const turn: Turn = {
  id: 't1',
  question: '질문',
  model: 'pro',
  format: 'concise',
  text: '사용자는 **30일분 이상의 통상임금(해고예고수당)**을 지급해야 합니다.',
  done: true,
  turnId: 'x',
}

test('bold next to Korean particles renders as bold', () => {
  render(<TurnView turn={turn} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  expect(screen.getByText('30일분 이상의 통상임금(해고예고수당)').tagName).toBe('STRONG')
})

test('feedback buttons only when feedback is on and the turn has an id', () => {
  const { rerender } = render(<TurnView turn={turn} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  expect(screen.queryByRole('button', { name: '좋아요' })).toBeNull()
  rerender(<TurnView turn={turn} selected reasons={[{ value: 'other', label: '기타' }]} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  expect(screen.getByRole('button', { name: '좋아요' })).toBeInTheDocument()
})
