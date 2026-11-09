import { useCallback, useEffect, useRef, useState } from 'react'
import { SourcesView } from '../components/SourcesView'
import { AnswerMarkdown } from '../components/TurnView'
import type { SourceItem } from '../types'
import { AdminApiError, adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'

const labels = { all: '전체', keep: '자동 유지', pending: '검토 대기', off: '끔', approved: '승인', revoked: '철회', draft: '초안' }
const reasons: Record<string, string> = {
  unchanged: '의존 조문과 표시본이 같습니다.', dependency_changed: '의존 조문이 변경되거나 사라졌습니다.',
  related_law_changed: '같은 법령의 다른 조문이 변경되었습니다.', policy_changed: '표시 정책이 변경되었습니다.',
  protection_changed: '보호 분할이 변경되었습니다.', bundle_missing: 'P07 번들이 설정되지 않았습니다.',
  protected_question: '보호 질문과 겹칩니다.', render_unavailable: '원문 또는 표시본을 확인할 수 없습니다.',
  display_changed: '현재 조문으로 렌더링한 표시본이 달라졌습니다.', awaiting_approval: '이전 릴리스부터 검토가 필요합니다.',
  withdrawn: '철회한 답변입니다.',
}
type Verdict = Exclude<keyof typeof labels, 'all'>
type Mode = 'off' | 'shadow' | 'on'
type Item = { answer_id: string; answer_key: string; status: Verdict; reason: string | null; release_id: string | null; question_count: number }
type Index = { mode: Mode; generation: number; namespace: string; active_release: string | null; approval_id: string | null; disabled_reason: string | null; items: Item[] }
type Article = { law: string; article_no: string; title: string; kind: string; text: string; deleted: string }
type Detail = {
  answer_id: string; answer_key: string; status: Verdict; generation: number; mode: Mode; text: string; preview: string | null
  raw_answer: string | null
  preview_sha256: string | null; disabled_reason: string | null; cards: SourceItem[]
  questions: { query_id: string; question: string; status: string }[]
  changes: { article_id: string; before: Article | null; after: Article | null }[]
  audit: { action: string; actor: string; reason: string; at: string }[]
}
type Action = 'approve' | 'revoke_answer' | 'revoke_query' | 'mode'
type Reviewed = { generation: number; items: Record<string, { hash: string; key: string; checked: boolean }> }
const BATCH_LIMIT = 50
const STACKED_WORKSPACE = '(max-width: 850px)' // Matches the layout breakpoint in admin.css.
const reasonHint = '변경 사유를 입력하세요'

function focusInView(target: HTMLElement, block: ScrollLogicalPosition) {
  target.focus({ preventScroll: true })
  target.scrollIntoView({ block })
}

function VerdictLabel({ status }: { status: Verdict }) {
  return <span className="cache-badge" data-status={status}>{labels[status]}</span>
}

function ArticleSide({ title, article }: { title: string; article: Article | null }) {
  return <section><h5>{title}</h5>{article ? <>
    <p>{article.law} {article.article_no} {article.title} ({article.kind}{article.deleted === '1' ? ' · 삭제' : ''})</p>
    <pre>{article.text}</pre>
  </> : <p>조문 없음</p>}</section>
}

export function CachePanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [filter, setFilter] = useState<keyof typeof labels>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [reviewed, setReviewed] = useState<Reviewed | null>(null)
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState('')
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const catalogHeading = useRef<HTMLHeadingElement>(null)
  const selectedAnswerButton = useRef<HTMLButtonElement>(null)
  const pendingDetailFocus = useRef<string | null>(null)
  const loadIndex = useCallback((signal: AbortSignal) => adminGet<Index>(`/cache?status=${filter}`, signal), [filter])
  const loadDetail = useCallback((signal: AbortSignal) => selected
    ? adminGet<Detail>(`/cache/answers/${encodeURIComponent(selected)}`, signal)
    : Promise.resolve(null), [selected])
  const index = useAdminRead(loadIndex, onUnauthorized)
  const detail = useAdminRead(loadDetail, onUnauthorized)
  const mutation = useAdminMutation(onUnauthorized)
  const info = index.data
  const answer = detail.data
  const needsReason = !reason.trim()
  const blocked = mutation.busy || needsReason
  const items = reviewed && reviewed.generation === info?.generation ? reviewed.items : {}
  const failure = mutation.failure?.error
  const failedId = failure instanceof AdminApiError ? failure.answerId : undefined
  const failureMessage = failedId ? adminMessage(failure, items[failedId]?.key) : mutation.error
  const detailReasonHint = needsReason && !mutation.busy && answer &&
    (answer.status !== 'revoked' || answer.questions.some(query => query.status !== 'revoked'))
    ? 'cache-detail-reason' : undefined
  const approvals = Object.entries(items).flatMap(([answer_id, item]) =>
    item.checked ? [{ answer_id, preview_sha256: item.hash }] : [])
  const modes = (['off', 'shadow', 'on'] as const).map(mode => ({
    mode, enabled: !!info && mode !== info.mode && (mode === 'off' || (!info.disabled_reason && !!info.approval_id)),
  }))

  useEffect(() => {
    if (!answer || answer.answer_id !== selected) return
    const { answer_id, answer_key, generation, preview_sha256 } = answer
    setReviewed(previous => {
      const items = { ...(previous?.generation === generation ? previous.items : {}) }
      const prior = items[answer_id]
      if (preview_sha256) {
        items[answer_id] = { hash: preview_sha256, key: answer_key, checked: prior?.hash === preview_sha256 && prior.checked }
      } else delete items[answer_id]
      return { generation, items }
    })
  }, [answer, selected])

  useEffect(() => {
    if (!answer || answer.answer_id !== selected || pendingDetailFocus.current !== selected) return
    pendingDetailFocus.current = null
    if (detailHeading.current && window.matchMedia(STACKED_WORKSPACE).matches) {
      focusInView(detailHeading.current, 'start')
    }
  }, [answer, selected])

  function openAnswer(answerId: string) {
    const stacked = window.matchMedia(STACKED_WORKSPACE).matches
    const heading = answerId === selected ? detailHeading.current : null
    pendingDetailFocus.current = stacked && !heading ? answerId : null
    if (stacked && heading) focusInView(heading, 'start')
    setSelected(answerId)
  }

  function returnToCatalog() {
    const target = selectedAnswerButton.current ?? catalogHeading.current
    if (target) focusInView(target, 'nearest')
  }

  function toggle(answerId: string, checked: boolean) {
    setReviewed(previous => {
      const item = previous?.items[answerId]
      if (!previous || !item) return previous
      return { ...previous, items: { ...previous.items, [answerId]: { ...item, checked } } }
    })
  }

  async function save(path: string, fields: Record<string, unknown>, approvedIds: string[] = []) {
    setNotice('')
    const result = await mutation.run<{ generation: number }>(path, { ...fields, reason: reason.trim() })
    if (result) {
      setReason('')
      setReviewed(null)
      setNotice('변경을 저장했습니다.')
      setSelected(current => current && approvedIds.includes(current) ? null : current)
      index.retry()
      detail.retry()
    }
  }

  function change(action: Action, target: string, generation: number, preview_sha256?: string) {
    return save('/cache/actions', { action, target, generation, preview_sha256 }, action === 'approve' ? [target] : [])
  }

  return <section className="cache-panel" aria-label="정확 일치 캐시 관리">
    <header className="cache-header">
      <div>
        <h2>정확 일치 캐시</h2>
        <p className="cache-muted">표시본과 연결 질문을 검토합니다.</p>
      </div>
      {info && <button className="cache-button" type="button" disabled={mutation.busy}
        onClick={() => {
          pendingDetailFocus.current = null
          index.retry()
          detail.retry()
          mutation.invalidate()
        }}>새로고침</button>}
    </header>
    {index.error != null && <p className="cache-message" role="alert">{adminMessage(index.error)}</p>}
    {!info && index.error == null && <p className="cache-muted" role="status">캐시 목록을 불러오는 중입니다.</p>}
    {info && <>
      <div className="cache-surface cache-controls">
        <dl className="cache-metadata">
          <div><dt>현재 모드</dt>{' '}<dd><span className="cache-badge cache-mode" data-status={info.mode}>{info.mode}</span></dd></div>
          <div><dt>활성 릴리스</dt><dd>{info.active_release ?? '없음'}</dd></div>
          <div><dt>namespace</dt><dd className="cache-id">{info.namespace}</dd></div>
          <div className="cache-approval-id"><dt>승인 묶음</dt><dd className="cache-id">{info.approval_id ?? '없음'}</dd></div>
        </dl>
        {info.disabled_reason && <p className="cache-inline-note">{info.disabled_reason}</p>}
        <section className="cache-mode-controls" aria-label="캐시 모드 전환">
          <label className="cache-field">변경 사유
            <input value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} />
          </label>
          <div className="cache-button-group">
            {modes.map(({ mode, enabled }) => <button className="cache-button" type="button" key={mode}
              disabled={blocked || !enabled}
              aria-describedby={enabled && needsReason && !mutation.busy ? 'cache-mode-reason' : undefined}
              onClick={() => void change('mode', mode, info.generation)}>모드 {mode}</button>)}
          </div>
          {needsReason && !mutation.busy && modes.some(({ enabled }) => enabled) &&
            <span className="cache-reason-hint" id="cache-mode-reason">{reasonHint}</span>}
        </section>
      </div>
      {failureMessage && <p className="cache-message" role="alert">{failureMessage}</p>}
      {notice && <p className="cache-message" role="status">{notice}</p>}
    </>}
    <div className="cache-workspace">
      {info && <section className="cache-surface cache-catalog" aria-labelledby="cache-catalog-heading">
        <header className="cache-catalog-header">
          <div className="cache-heading-row">
            <h3 id="cache-catalog-heading" ref={catalogHeading} tabIndex={-1}>캐시 답변</h3>
            <span className="cache-muted">{info.items.length}개</span>
          </div>
          <div className="cache-list-controls">
            <label className="cache-field">판정 필터
              <select value={filter} onChange={e => setFilter(e.target.value as keyof typeof labels)}>
                {Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <button className="cache-button cache-button-primary" type="button"
              disabled={mutation.busy || !!info.disabled_reason || !approvals.length}
              onClick={() => void save('/cache/approvals', { generation: info.generation, items: approvals },
                approvals.map(item => item.answer_id))}>선택 승인</button>
          </div>
          <div className="cache-list-notes">
            <p>{approvals.length}개 선택 (최대 {BATCH_LIMIT}개)</p>
            <p className="cache-muted">상세에서 표시본을 확인한 답변을 선택할 수 있습니다.</p>
            {info.mode === 'on' && approvals.length > 0 && <p className="cache-inline-note">승인하면 바로 사용자에게 반환됩니다</p>}
          </div>
        </header>
        <ul className="cache-answer-list" role="list" aria-label="캐시 답변 목록">{info.items.map(item =>
          <li className="cache-answer-row" key={item.answer_id}
            data-current={item.answer_id === selected} data-failed={item.answer_id === failedId}>
            <label className="cache-check">
              <input type="checkbox" aria-label={`${item.answer_key} 선택`} checked={items[item.answer_id]?.checked ?? false}
                disabled={mutation.busy || !!info.disabled_reason || !items[item.answer_id] ||
                  (!items[item.answer_id].checked && approvals.length >= BATCH_LIMIT)}
                onChange={e => toggle(item.answer_id, e.target.checked)} />
            </label>
            <button className="cache-answer-select" type="button" aria-label={`${item.answer_key} · ${labels[item.status]}`}
              ref={item.answer_id === selected ? selectedAnswerButton : undefined}
              aria-current={item.answer_id === selected ? 'true' : undefined} onClick={() => openAnswer(item.answer_id)}>
              <span className="cache-answer-title"><strong>{item.answer_key}</strong><VerdictLabel status={item.status} /></span>
              <span className="cache-answer-meta">질문 {item.question_count}개 · {item.release_id ?? '판정 전'}</span>
              {item.reason && <span className="cache-answer-meta">{reasons[item.reason] ?? item.reason}</span>}
              {item.answer_id === failedId && <span className="cache-error-label">승인 실패</span>}
            </button>
          </li>)}</ul>
        {!info.items.length && <p className="cache-empty">이 조건에 해당하는 답변이 없습니다.</p>}
      </section>}
      {detail.error != null && <p className="cache-detail cache-message" role="alert">{adminMessage(detail.error)}</p>}
      {info && !selected && <div className="cache-surface cache-detail cache-empty-detail">
        <h3>답변을 선택하세요</h3>
        <p className="cache-muted">목록에서 답변을 열면 표시본과 연결 질문을 확인할 수 있습니다.</p>
      </div>}
      {answer && answer.answer_id === selected && <section className="cache-surface cache-detail" aria-label="캐시 답변 상세">
        <header className="cache-detail-header">
          <p className="cache-muted">답변 상세</p>
          <div className="cache-heading-row">
            <h3 ref={detailHeading} tabIndex={-1} aria-label={`${answer.answer_key} · ${labels[answer.status]}`}>{answer.answer_key}</h3>
            <VerdictLabel status={answer.status} />
          </div>
        </header>
        <section className="cache-detail-section">
          <h4>{answer.preview ? '현재 릴리스 표시본' : '저장된 표시본'}</h4>
          <div className="answer cache-preview"><AnswerMarkdown text={answer.preview ?? answer.text} /></div>
          {answer.raw_answer && <details className="cache-disclosure">
            <summary>답변 원문</summary><pre>{answer.raw_answer}</pre>
          </details>}
          {answer.preview && answer.preview !== answer.text && <details className="cache-disclosure">
            <summary>이전 표시본</summary>
            <div className="answer"><AnswerMarkdown text={answer.text} /></div>
          </details>}
        </section>
        <section className="cache-detail-section">
          <div className="cache-heading-row">
            <h4>연결 질문</h4>
            <span className="cache-muted">{answer.questions.length}개</span>
          </div>
          <ul className="cache-question-list" role="list" aria-label="연결 질문 목록">
            {answer.questions.map(query => <li key={query.query_id}>
              <span className="cache-question-text">{query.question}</span>
              {query.status === 'revoked' ? <span className="cache-badge" data-status="revoked">철회됨</span> :
                <button className="cache-button cache-button-quiet" type="button" disabled={blocked} aria-describedby={detailReasonHint}
                  onClick={() => void change('revoke_query', query.query_id, answer.generation)}>질문 철회</button>}
            </li>)}
          </ul>
        </section>
        {answer.cards.length > 0 && <div className="cache-detail-section">
          <SourcesView title="표시 근거" sources={{ groups: [{ label: null, open: true, items: answer.cards }] }} />
        </div>}
        <section className="cache-detail-section">
          <h4>바뀐 조문</h4>
          {!answer.changes.length && <p className="cache-muted">기록된 조문 변경이 없습니다.</p>}
          {answer.changes.map(change => <details className="cache-disclosure" key={change.article_id}>
            <summary>{change.article_id} 비교</summary>
            <div className="cache-compare"><ArticleSide title="이전 본문" article={change.before} /><ArticleSide title="이후 본문" article={change.after} /></div>
          </details>)}
        </section>
        <section className="cache-detail-section cache-detail-actions" aria-label="캐시 답변 동작">
          <div className="cache-detail-control">
            <label className="cache-field">상세 변경 사유
              <input value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} />
            </label>
            <div className="cache-button-group">
              {(answer.status === 'pending' || answer.status === 'draft') && <button className="cache-button cache-button-primary" type="button"
                disabled={mutation.busy || !answer.preview_sha256 || !!answer.disabled_reason}
                onClick={() => void change('approve', answer.answer_id, answer.generation, answer.preview_sha256 ?? undefined)}>
                {answer.status === 'pending' ? '표시본 확인 및 재승인' : '표시본 확인 및 승인'}
              </button>}
              {answer.status !== 'revoked' && <button className="cache-button cache-button-quiet" type="button"
                disabled={blocked} aria-describedby={detailReasonHint}
                onClick={() => void change('revoke_answer', answer.answer_id, answer.generation)}>답변 철회</button>}
            </div>
          </div>
          {detailReasonHint && <p className="cache-reason-hint" id={detailReasonHint}>{reasonHint}</p>}
          {answer.mode === 'on' && answer.preview_sha256 && <p className="cache-inline-note">
            {answer.status === 'pending' ? '재승인 즉시 사용자에게 반환됩니다.' : '승인하면 바로 사용자에게 반환됩니다'}
          </p>}
          {answer.disabled_reason && <p className="cache-inline-note">{answer.disabled_reason}</p>}
          <p className="cache-muted">답변 정정은 CLI replace로 진행하세요.</p>
        </section>
        <section className="cache-detail-section">
          <h4>감사 이력</h4>
          <ul className="cache-audit-list" role="list">{answer.audit.map((event, index) =>
            <li key={`${event.at}-${index}`}>
              <div className="cache-audit-meta">
                <time dateTime={event.at}>{event.at}</time><span>{event.actor}</span><span>{event.action}</span>
              </div>
              <p>{event.reason}</p>
            </li>)}</ul>
        </section>
        <footer className="cache-detail-section cache-detail-return">
          <button className="cache-button" type="button" onClick={returnToCatalog}>목록으로</button>
        </footer>
      </section>}
    </div>
  </section>
}
