import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CalendarChange } from '@lawbot/ui'
import type { CalendarLoader } from '../law-calendar/types'
import type { ErpItem, ErpUpdates } from '../law-calendar/api-types'
import ErpPage from './ErpPage'
import type { ErpLoader } from './data'

const base: ErpItem = {
  id: 'pension', sourceKey: 'INS-01', sourceTitle: '[예시] 연금 상·하한', category: 'insurance', kind: 'admrule',
  title: '[예시] 가상 연금 상한액', agency: '가상부', lawName: null, article: null, issuedNo: '2026-1',
  issuedOn: '2026-02-02', effectiveFrom: '2026-07-01', asOf: null, excerpt: '하한액 : 100천원\n상한액 : 900천원',
  sourceUrl: 'https://www.law.go.kr/행정규칙/예시', attachments: [], contentSha256: 'a'.repeat(64),
  collectedAt: '2026-11-12T20:00:00+09:00', reviewedAt: '2026-11-12T20:05:00+09:00',
}
const feed: ErpUpdates = {
  state: 'ok',
  generatedAt: '2026-11-12T20:06:00+09:00',
  items: [
    base,
    { ...base, id: 'next', sourceKey: 'INS-03', title: '[예시] 2027년도 가상 요율', effectiveFrom: '2027-01-01' },
    {
      ...base, id: 'table', sourceKey: 'TAX-01', kind: 'form', title: '[예시] 가상 간이세액표', agency: null,
      lawName: '[예시] 가상법 시행령', article: '별표 2', issuedNo: null, issuedOn: null, effectiveFrom: null,
      asOf: '2026-10-01', excerpt: null,
      attachments: [{ title: '[예시] 가상 간이세액표', pdfUrl: 'https://www.law.go.kr/LSW/flDownload.do?flSeq=1', sha256: 'c1b533d6'.padEnd(64, '0'), bytes: 397806 }],
    },
  ],
  relatedLaws: ['[예시] 가상법 시행령'],
  history: [
    { itemId: 'pension', sourceKey: 'INS-01', title: base.title, at: '2026-11-12T20:00:00+09:00', action: 'collected', origin: 'admrule-kr@0123456789ab' },
    { itemId: 'table', sourceKey: 'TAX-01', title: '[예시] 가상 간이세액표', at: '2026-11-12T20:01:00+09:00', action: 'attached', origin: 'file sha256:c1b533d60000' },
    { itemId: 'pension', sourceKey: 'INS-01', title: base.title, at: '2026-11-12T20:05:00+09:00', action: 'approved', origin: null },
  ],
}
const change: CalendarChange = {
  id: 'related', kind: 'effective', date: '2026-11-20', lawName: '[예시] 가상법 시행령', article: '제189조',
  summary: '관련 시행 요약', status: '시행', before: null, after: null, comparable: false, publishedOn: null,
  promulgatedOn: null, effectiveOn: '2026-11-20', commentDeadline: null, sourceUrl: null, collectedAt: null, verifiedAt: null,
}
const lawLoader: CalendarLoader = async (month) => ({
  changes: month === '2026-11' ? [change, { ...change, id: 'other', lawName: '[예시] 무관법', summary: '무관 요약' }] : [],
  demo: true, delayed: false, collectedAt: null, verifiedAt: null,
})
const loader: ErpLoader = async () => feed

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

test('splits current and upcoming items and shows dates, excerpts and file hashes', async () => {
  render(<ErpPage loader={loader} lawLoader={lawLoader} today="2026-11-12" />)
  const current = await screen.findByRole('region', { name: '현재 적용 중' })
  const upcoming = screen.getByRole('region', { name: '적용 예정' })
  expect(within(current).getByRole('heading', { name: '[예시] 가상 연금 상한액' })).toBeInTheDocument()
  expect(within(current).getByText('2026-07-01')).toBeInTheDocument()
  expect(within(current).getByText(/상한액 : 900천원/)).toBeInTheDocument()
  expect(within(current).getByText('현행 법령본 기준일')).toBeInTheDocument()
  expect(within(current).getByText(/SHA-256 c1b533d6/)).toBeInTheDocument()
  expect(within(upcoming).getByRole('heading', { name: '[예시] 2027년도 가상 요율' })).toBeInTheDocument()
  expect(within(current).queryByText('[예시] 2027년도 가상 요율')).not.toBeInTheDocument()
})

test('related law schedule keeps only laws named by the feed', async () => {
  render(<ErpPage loader={loader} lawLoader={lawLoader} today="2026-11-12" />)
  const related = await screen.findByRole('region', { name: /관련 법령 일정/ })
  expect(await within(related).findByText('관련 시행 요약')).toBeInTheDocument()
  expect(within(related).queryByText('무관 요약')).not.toBeInTheDocument()
  await userEvent.click(within(related).getByRole('button', { name: '다음 달' }))
  expect(await within(related).findByText('이 달에 관련 법령의 입법예고·시행 조문이 없습니다.')).toBeInTheDocument()
})

test('history button lists what was collected and when', async () => {
  render(<ErpPage loader={loader} lawLoader={lawLoader} today="2026-11-12" />)
  await userEvent.click(await screen.findByRole('button', { name: '수집 이력' }))
  const dialog = screen.getByRole('dialog', { name: '수집 이력' })
  const rows = within(dialog).getAllByRole('row').slice(1)
  expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))).toEqual([
    ['2026-11-12 20:05', 'INS-01 [예시] 가상 연금 상한액', '공개 승인', '—'],
    ['2026-11-12 20:01', 'TAX-01 [예시] 가상 간이세액표', '원문 PDF 등록', 'file sha256:c1b533d60000'],
    ['2026-11-12 20:00', 'INS-01 [예시] 가상 연금 상한액', '새 버전 수집', 'admrule-kr@0123456789ab'],
  ])
  await userEvent.click(within(dialog).getByRole('button', { name: '수집 이력 닫기' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test.each([
  ['missing', 'ERP 자료가 아직 준비되지 않았습니다.'],
  ['invalid', 'ERP 자료 파일을 확인하지 못했습니다. 법령 캘린더는 그대로 이용할 수 있습니다.'],
] as const)('%s feed shows a notice and no items', async (state, message) => {
  render(<ErpPage loader={async () => ({ state })} lawLoader={lawLoader} today="2026-11-12" />)
  expect(await screen.findByText(message)).toBeInTheDocument()
  const hometax = screen.getByRole('link', { name: '홈택스 공식 홈' })
  expect(hometax).toHaveAttribute('href', 'https://www.hometax.go.kr/')
  expect(hometax).toHaveAttribute('rel', 'noopener noreferrer')
  expect(screen.getByText('전체메뉴 → 기타 → 고객센터 → 자료실')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '현재 적용 중' })).not.toBeInTheDocument()
})

test('load failure can be retried', async () => {
  let calls = 0
  const flaky: ErpLoader = async () => { calls += 1; if (calls === 1) throw new Error('down'); return feed }
  render(<ErpPage loader={flaky} lawLoader={lawLoader} today="2026-11-12" />)
  await userEvent.click(await screen.findByRole('button', { name: '다시 시도' }))
  expect(await screen.findByRole('region', { name: '현재 적용 중' })).toBeInTheDocument()
})
