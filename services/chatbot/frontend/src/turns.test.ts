import { applyEvent, newTurn } from './turns'

const sources = { groups: [{ label: null, open: true, items: [{ title: 'A', scores: 's', body: 'b', decree: false }] }] }

test('events build up one turn', () => {
  let t = newTurn('q', 'pro', 'concise')
  expect(t.done).toBe(false)
  t = applyEvent(t, 'status', { text: '⏳ 조문 검색 중…' })
  expect(t.status).toBe('⏳ 조문 검색 중…')
  t = applyEvent(t, 'sources', sources)
  t = applyEvent(t, 'answer', { text: '부분', draft: true })
  expect(t).toMatchObject({ text: '부분', draft: true })
  expect(t.status).toBeUndefined() // the answer replaces the status line
  t = applyEvent(t, 'stats', { text: '검색 0.1초' })
  t = applyEvent(t, 'done', { text: '최종', finish: 'stop', turn_id: 'abc' })
  expect(t).toMatchObject({ text: '최종', draft: false, done: true, turnId: 'abc', stats: '검색 0.1초', sources })
})

test('error ends the turn and keeps partial text', () => {
  let t = applyEvent(newTurn('q', 'pro', 'full'), 'answer', { text: '부분' })
  t = applyEvent(t, 'error', { kind: 'generation', message: '답변을 생성하지 못했습니다' })
  expect(t).toMatchObject({ done: true, text: '부분', error: '답변을 생성하지 못했습니다' })
})

test('unknown events are ignored', () => {
  const t = newTurn('q', 'pro', 'full')
  expect(applyEvent(t, 'other', {})).toBe(t)
})

test('calculation arrives ahead of the model text and keeps its check on completion', () => {
  const result = { calculator: 'medical_expense_credit', status: 'calculated', results: [{ label: '의료비 세액공제', value: 75000 }] }
  let t = applyEvent(newTurn('q', 'pro', 'concise'), 'calculation', result)
  expect(t).toMatchObject({ text: '', done: false, calculation: result })
  t = applyEvent(t, 'answer', { text: '부분' })
  t = applyEvent(t, 'calculation_check', { status: 'mismatch' })
  t = applyEvent(t, 'done', { text: '최종', turn_id: null })
  expect(t).toMatchObject({ text: '최종', done: true, calculation: result, calculationCheck: 'mismatch' })
})
