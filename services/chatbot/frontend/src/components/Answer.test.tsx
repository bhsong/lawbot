import { fireEvent, render, screen, within } from '@testing-library/react'
import { AnswerMarkdown, TurnView } from './TurnView'
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

test('answer Markdown preserves image alt text without loading the image', () => {
  render(<AnswerMarkdown text="![도표](https://example.invalid/image.png) [원문](https://example.invalid/article)" />)
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByText('도표')).toBeVisible()
  expect(screen.getByRole('link', { name: '원문' })).toHaveAttribute('href', 'https://example.invalid/article')
})

test.each([undefined, { writeText: () => Promise.reject(new Error('Clipboard permission denied')) }])(
  'copy reports when the clipboard is unavailable or rejects',
  async (clipboard) => {
    vi.stubGlobal('navigator', { clipboard })
    try {
      render(<TurnView turn={turn} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: '답변 복사' }))
      expect(await screen.findByText('답변을 복사하지 못했습니다.')).toHaveAttribute('role', 'status')
      expect(screen.queryByRole('button', { name: '복사됨' })).not.toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
    }
  },
)

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

test('renders labor day and time units and an assumed salary source', () => {
  const calculated: Turn = {
    ...turn,
    calculation: {
      calculator: 'annual_leave_days', status: 'calculated', year: 2026,
      results: [{ label: '연차유급휴가 일수', value: 15, unit: '일' }],
      inputs: [{ label: '주 소정근로시간', value: 40, unit: '시간', source: '질문: 40시간' }],
      steps: [{ label: '연차', formula: '출근율 80% 이상', value: 15, unit: '일', article: '근로기준법 제60조' }],
    },
    calculationCheck: 'missing',
  }
  render(<TurnView turn={calculated} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  const card = screen.getByRole('region', { name: '계산 결과' })
  expect(within(card).getByText('15일', { selector: 'strong' })).toBeVisible()
  expect(within(card).getByRole('alert')).toHaveTextContent('일수')
  fireEvent.click(within(card).getByText('입력과 계산 단계'))
  expect(within(card).getByText('40시간')).toBeVisible()
})

test('shows the condition on each monthly severance estimate', () => {
  const calculated: Turn = {
    ...turn,
    calculation: {
      calculator: 'severance_pay', status: 'calculated',
      results: [
        { label: '평균임금 기준 퇴직금 추정', value: 9000000, condition: '평균임금 기준 하한' },
        { label: '통상임금 하한 적용 퇴직금 추정', value: 10334928, condition: '월급 전액이 통상임금인 경우' },
      ],
      message: '기본급만이면 약 10,334,928원, 그 외에는 평균임금 기준 약 9,000,000원 이상입니다.',
    },
  }
  render(<TurnView turn={calculated} selected reasons={null} onSelect={vi.fn()} onRetry={vi.fn()} onFeedback={vi.fn()} onShowSources={vi.fn()} />)
  const card = screen.getByRole('region', { name: '계산 결과' })
  const lower = within(card).getByText('9,000,000원', { selector: 'strong' }).closest('dd')!
  const ordinary = within(card).getByText('10,334,928원', { selector: 'strong' }).closest('dd')!
  expect(within(lower).getByText('평균임금 기준 하한')).toBeVisible()
  expect(within(ordinary).getByText('월급 전액이 통상임금인 경우')).toBeVisible()
  expect(within(card).getByText(/기본급만이면 약 10,334,928원/)).toBeVisible()
})
