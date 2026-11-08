import { useCallback, useEffect, useId, useState } from 'react'
import { AdminApiError, adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'
import { FeedbackCorrectionDialog } from './FeedbackCorrectionDialog'
import type { FeedbackDetailData, FeedbackItem, FeedbackPage, Label, PromotionResult, Status } from './types'
import { decisionLabels, reasonLabels, statusLabels, verdictLabels } from './types'

const badgeLabels: Record<string, string> = {
  leak_test: '시험 데이터 겹침', leak_val: '검증 데이터 겹침', leak_eval: '평가 전용 문항 겹침',
  overlap_train: '훈련 데이터 겹침', label_changed: '라벨 바뀜', corpus_changed: '조문 버전 다름',
}

type Filter = { status: Status | 'all'; reason: string; q: string }
const initialFilter: Filter = { status: 'pending', reason: '', q: '' }
type Listing = { items: FeedbackItem[]; next: string | null; loading: boolean; error: string }
const initialListing: Listing = { items: [], next: null, loading: true, error: '' }

function OpenFeedback({ item, onClose, onSaved, onUnauthorized }: {
  item: FeedbackItem; onClose: () => void; onSaved: (message: string) => void; onUnauthorized: () => void
}) {
  const load = useCallback((signal: AbortSignal) =>
    adminGet<FeedbackDetailData>(`/feedbacks/${encodeURIComponent(item.feedback_id)}`, signal), [item.feedback_id])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)
  if (error != null) return <p role="alert" className="admin-error">{adminMessage(error)}{' '}
    <button type="button" onClick={retry}>다시 시도</button> <button type="button" onClick={onClose}>닫기</button>
  </p>
  if (!data) return <p role="status">피드백 상세를 불러오는 중입니다.</p>
  return <FeedbackCorrectionDialog item={item} detail={data} onClose={onClose} onSaved={onSaved} onUnauthorized={onUnauthorized} />
}

export function FeedbackList({ label, onUnauthorized }: { label: Label; onUnauthorized: () => void }) {
  const titleId = useId()
  const likes = label === 'like'
  const [filter, setFilter] = useState(initialFilter)
  const [search, setSearch] = useState('')
  const [cursor, setCursor] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [list, setList] = useState(initialListing)
  const [opened, setOpened] = useState<FeedbackItem | null>(null)
  const [selected, setSelected] = useState(new Map<string, string>())
  const [results, setResults] = useState<{ text: string; ok: boolean }[]>([])
  const [notice, setNotice] = useState('')
  const batch = useAdminMutation(onUnauthorized)

  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ label, status: filter.status })
    if (filter.reason) query.set('reason', filter.reason)
    if (filter.q) query.set('q', filter.q)
    if (cursor) query.set('cursor', cursor)
    setList((old) => cursor ? { ...old, loading: true, error: '' } : initialListing)
    adminGet<FeedbackPage>(`/feedbacks?${query}`, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return
        setList((old) => {
          const items = cursor ? [...old.items] : []
          const seen = new Set(items.map((item) => item.feedback_id))
          items.push(...data.items.filter((item) => !seen.has(item.feedback_id)))
          return { items, next: data.next_cursor, loading: false, error: '' }
        })
      },
      (error: unknown) => {
        if (controller.signal.aborted) return
        if (error instanceof AdminApiError && error.status === 401) onUnauthorized()
        else setList((old) => ({ ...old, loading: false, error: adminMessage(error) }))
      },
    )
    return () => controller.abort()
  }, [filter, label, cursor, revision, onUnauthorized])

  function refresh() {
    setCursor(null)
    setRevision((value) => value + 1)
  }

  function changeFilter(next: Filter) {
    batch.invalidate()
    setSelected(new Map())
    setOpened(null)
    setNotice('')
    setCursor(null)
    setFilter(next)
  }

  function select(item: FeedbackItem) {
    batch.invalidate()
    setSelected((old) => {
      const next = new Map(old)
      if (next.has(item.feedback_id)) next.delete(item.feedback_id)
      else if (next.size < 50) next.set(item.feedback_id, item.question_preview)
      return next
    })
  }

  async function promote() {
    const result = await batch.run<{ results: PromotionResult[] }>('/feedbacks/promote-up', { feedback_ids: [...selected.keys()] })
    if (!result) return
    setResults(result.results.map((row) => {
      const question = selected.get(row.feedback_id) ?? row.feedback_id
      const message = row.ok ? `승격 완료 (${row.qa_uid})` : adminMessage(new AdminApiError(422, row.code, row.detail))
      return { text: `${question} · ${message}`, ok: row.ok }
    }))
    setSelected(new Map())
    refresh()
  }

  return <section aria-labelledby={titleId} className="admin-card">
    <h2 id={titleId}>{likes ? '좋아요' : '문제 신고'}</h2>
    <form className="admin-filters" onSubmit={(event) => {
      event.preventDefault()
      changeFilter({ ...filter, q: search.trim() })
    }}>
      <fieldset disabled={batch.busy} className="admin-filter-fields">
        <label>상태<select value={filter.status} onChange={(event) => changeFilter({ ...filter, status: event.target.value as Filter['status'] })}>
          {Object.entries(statusLabels).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
          <option value="all">전체</option>
        </select></label>
        {!likes && <label>신고 사유<select value={filter.reason} onChange={(event) => changeFilter({ ...filter, reason: event.target.value })}>
          <option value="">모든 사유</option>
          {Object.entries(reasonLabels).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select></label>}
        <label>질문 검색<input type="search" maxLength={100} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <button type="submit">검색</button>
      </fieldset>
    </form>
    {notice && <p role="status">{notice}</p>}
    {likes && <button type="button" disabled={!selected.size || batch.busy} onClick={() => void promote()}>
      선택 승격 ({selected.size})
    </button>}
    {batch.busy && <p role="status">승격을 처리 중입니다.</p>}
    {batch.error && <p role="alert" className="admin-error">{batch.error}</p>}
    {results.length > 0 && <ul aria-label="일괄 승격 결과" aria-live="polite">{results.map(({ text, ok }, index) => (
      <li key={index} className={ok ? undefined : 'admin-error'}>{text}</li>
    ))}</ul>}
    {list.loading && <p role="status">피드백을 불러오는 중입니다.</p>}
    {list.error && <p role="alert" className="admin-error">{list.error}{' '}
      <button type="button" onClick={() => setRevision((value) => value + 1)}>다시 시도</button>
    </p>}
    {!list.loading && !list.error && !list.items.length && <p>표시할 피드백이 없습니다.</p>}
    {list.items.length > 0 && <ul className="admin-feedback-items" aria-label="피드백 목록">{list.items.map((item) => (
      <li key={item.feedback_id}>
        <div className="admin-row">
          {likes && <input type="checkbox" aria-label={`${item.question_preview} 선택`}
            checked={selected.has(item.feedback_id)} disabled={batch.busy || (!selected.has(item.feedback_id) && selected.size >= 50)}
            onChange={() => select(item)} />}
          <button type="button" className="admin-question" disabled={batch.busy} onClick={() => setOpened(item)}>{item.question_preview}</button>
        </div>
        <p className="admin-badges">
          <span>{statusLabels[item.status]}</span>
          {item.reason && <span>{reasonLabels[item.reason] ?? item.reason}</span>}
          {item.warnings.map((code) => badgeLabels[code] && <span key={code}>{badgeLabels[code]}</span>)}
        </p>
        {item.latest_review && <p className="admin-meta">
          {verdictLabels[item.latest_review.verdict]} / {decisionLabels[item.latest_review.decision]} · {item.latest_review.reviewer}
        </p>}
        <time dateTime={item.ts} className="admin-meta">{item.ts}</time>
      </li>
    ))}</ul>}
    {list.next && !list.error && <button type="button" disabled={list.loading || batch.busy} onClick={() => setCursor(list.next)}>더 보기</button>}
    {opened && <OpenFeedback key={opened.feedback_id} item={opened} onUnauthorized={onUnauthorized} onClose={() => setOpened(null)}
      onSaved={(message) => {
        setOpened(null)
        setNotice(message)
        batch.invalidate()
        setSelected(new Map())
        refresh()
      }} />}
  </section>
}
