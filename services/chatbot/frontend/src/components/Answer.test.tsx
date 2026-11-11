import { fireEvent, render, screen, within } from '@testing-library/react'
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

test('renders the calculation above the answer with inputs, formulas, sources and warning', () => {
  const calculated: Turn = {
    ...turn,
    calculation: {
      calculator: 'medical_expense_credit', status: 'calculated', year: 2026,
      inputs: [
        { label: '총급여', value: 50000000, source: '질문: 5천만 원' },
        { label: '실손의료보험금', value: 0, source: '미입력(0)' },
      ],
      steps: [{ label: '공제액', formula: '1,500,000원 × 15%', value: 225000, article: '소득세법 제59조의4' }],
      results: [{ label: '의료비 세액공제', value: 225000 }],
    },
    calculationCheck: 'mismatch',
  }
  render(<TurnView turn={calculated} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  const result = screen.getByRole('region', { name: '계산 결과' })
  expect(result.compareDocumentPosition(screen.getByTestId('answer')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(within(result).getByText('225,000원', { selector: 'strong' })).toBeVisible()
  expect(within(result).getByText('미입력(0) 1건')).toBeVisible()
  expect(within(result).getByRole('alert')).toHaveTextContent('계산 표를 기준으로')
  fireEvent.click(within(result).getByText('입력과 계산 단계'))
  const inputs = within(result).getByRole('table', { name: '계산 입력' })
  const stages = within(result).getByRole('table', { name: '단계별 계산' })
  expect(within(inputs).getByRole('row', { name: '실손의료보험금 0원 미입력(0)' })).toBeVisible()
  expect(within(inputs).getByText('질문: 5천만 원')).toBeVisible()
  expect(within(stages).getByText('1,500,000원 × 15%')).toBeVisible()
  expect(within(stages).getByText('소득세법 제59조의4')).toBeVisible()
})

test.each(['guidance', 'none'] as const)('shows %s directly from the calculation event', (status) => {
  render(<TurnView turn={{ ...turn, text: '', done: false, calculation: {
    calculator: null, status, message: '총급여를 알려 주시면 계산할 수 있습니다.', articles: ['소득세법 제59조의4'],
  } }} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  const guidance = screen.getByRole('region', { name: '계산 안내' })
  expect(within(guidance).getByText('총급여를 알려 주시면 계산할 수 있습니다.')).toBeVisible()
  expect(within(guidance).getByText('소득세법 제59조의4')).toBeVisible()
  expect(within(guidance).queryByRole('table')).not.toBeInTheDocument()
})
