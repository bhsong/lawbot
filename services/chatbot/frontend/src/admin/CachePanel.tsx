import { useCallback, useState } from 'react'
import { SourcesView } from '../components/SourcesView'
import { AnswerMarkdown } from '../components/TurnView'
import type { SourceItem } from '../types'
import { adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'

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
  answer_id: string; answer_key: string; status: Verdict; generation: number; text: string; preview: string | null
  raw_answer: string | null
  preview_sha256: string | null; disabled_reason: string | null; cards: SourceItem[]
  questions: { query_id: string; question: string; status: string }[]
  changes: { article_id: string; before: Article | null; after: Article | null }[]
  audit: { action: string; actor: string; reason: string; at: string }[]
}
type Action = 'approve' | 'revoke_answer' | 'revoke_query' | 'mode'

function ArticleSide({ title, article }: { title: string; article: Article | null }) {
  return <section><h5>{title}</h5>{article ? <>
    <p>{article.law} {article.article_no} {article.title} ({article.kind}{article.deleted === '1' ? ' · 삭제' : ''})</p>
    <pre>{article.text}</pre>
  </> : <p>조문 없음</p>}</section>
}

export function CachePanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [filter, setFilter] = useState<keyof typeof labels>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState('')
  const loadIndex = useCallback((signal: AbortSignal) => adminGet<Index>(`/cache?status=${filter}`, signal), [filter])
  const loadDetail = useCallback((signal: AbortSignal) => selected
    ? adminGet<Detail>(`/cache/answers/${encodeURIComponent(selected)}`, signal)
    : Promise.resolve(null), [selected])
  const index = useAdminRead(loadIndex, onUnauthorized)
  const detail = useAdminRead(loadDetail, onUnauthorized)
  const mutation = useAdminMutation(onUnauthorized)
  const info = index.data
  const answer = detail.data
  const blocked = mutation.busy || !reason.trim()

  async function change(action: Action, target: string, generation: number, preview_sha256?: string) {
    setNotice('')
    const result = await mutation.run<{ generation: number }>('/cache/actions', {
      action, target, generation, reason: reason.trim(), preview_sha256,
    })
    if (result) {
      setReason('')
      setNotice('변경을 저장했습니다.')
      if (action === 'approve') setSelected(null)
      index.retry()
      detail.retry()
    }
  }

  return <section className="cache-panel" aria-label="정확 일치 캐시 관리">
    <h2>정확 일치 캐시</h2>
    {index.error != null && <p role="alert">{adminMessage(index.error)}</p>}
    {!info && index.error == null && <p role="status">캐시 목록을 불러오는 중입니다.</p>}
    {info && <>
      <p>현재 모드 <strong>{info.mode}</strong> · namespace {info.namespace} · 활성 릴리스 {info.active_release ?? '없음'}</p>
      <p className="cache-id">승인 묶음 {info.approval_id ?? '없음'}</p>
      {info.disabled_reason && <p>{info.disabled_reason}</p>}
      <label>변경 사유 <input value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
      <div className="cache-actions" aria-label="캐시 모드 전환">
        {(['off', 'shadow', 'on'] as const).map(mode => <button type="button" key={mode}
          disabled={blocked || info.mode === mode || (mode !== 'off' && (!!info.disabled_reason || !info.approval_id)) || (mode === 'on' && info.mode !== 'shadow')}
          onClick={() => void change('mode', mode, info.generation)}>모드 {mode}</button>)}
        <button type="button" onClick={() => { index.retry(); detail.retry(); mutation.invalidate() }}>새로고침</button>
      </div>
      {mutation.error && <p role="alert">{mutation.error}</p>}
      {notice && <p role="status">{notice}</p>}
      <label>판정 필터 <select value={filter} onChange={e => setFilter(e.target.value as keyof typeof labels)}>
        {Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select></label>
      <ul aria-label="캐시 답변 목록">{info.items.map(item => <li key={item.answer_id}>
        <button type="button" onClick={() => setSelected(item.answer_id)}>{item.answer_key} · {labels[item.status]}</button>
        <span> 질문 {item.question_count}개 · {item.release_id ?? '판정 전'} {item.reason ? reasons[item.reason] ?? item.reason : ''}</span>
      </li>)}</ul>
      {!info.items.length && <p>이 조건에 해당하는 답변이 없습니다.</p>}
    </>}
    {detail.error != null && <p role="alert">{adminMessage(detail.error)}</p>}
    {answer && answer.answer_id === selected && <section aria-label="캐시 답변 상세">
      <h3>{answer.answer_key} · {labels[answer.status]}</h3>
      <h4>{answer.preview ? '현재 릴리스 표시본' : '저장된 표시본'}</h4>
      <div className="answer"><AnswerMarkdown text={answer.preview ?? answer.text} /></div>
      {answer.raw_answer && <details><summary>답변 원문</summary><pre>{answer.raw_answer}</pre></details>}
      {answer.preview && answer.preview !== answer.text && <details><summary>이전 표시본</summary>
        <div className="answer"><AnswerMarkdown text={answer.text} /></div></details>}
      {answer.cards.length > 0 && <SourcesView title="표시 근거" sources={{ groups: [{ label: null, open: true, items: answer.cards }] }} />}
      <h4>연결 질문</h4>
      <ul>{answer.questions.map(query => <li key={query.query_id}>{query.question} {query.status === 'revoked' ? '· 철회됨' :
        <button type="button" disabled={blocked} onClick={() => void change('revoke_query', query.query_id, answer.generation)}>질문 철회</button>}
      </li>)}</ul>
      <div className="cache-actions">
        {['pending', 'draft'].includes(answer.status) && <button type="button"
          disabled={blocked || !answer.preview_sha256 || !!answer.disabled_reason}
          onClick={() => void change('approve', answer.answer_id, answer.generation, answer.preview_sha256 ?? undefined)}>표시본 확인 및 재승인</button>}
        {answer.status !== 'revoked' && <button type="button" disabled={blocked}
          onClick={() => void change('revoke_answer', answer.answer_id, answer.generation)}>답변 철회</button>}
      </div>
      {answer.disabled_reason && <p>{answer.disabled_reason}</p>}
      <p>답변 정정은 CLI replace로 진행하세요.</p>
      <h4>바뀐 조문</h4>
      {!answer.changes.length && <p>기록된 조문 변경이 없습니다.</p>}
      {answer.changes.map(change => <details key={change.article_id}>
        <summary>{change.article_id} 비교</summary>
        <div className="cache-compare"><ArticleSide title="이전 본문" article={change.before} /><ArticleSide title="이후 본문" article={change.after} /></div>
      </details>)}
      <h4>감사 이력</h4>
      <ul>{answer.audit.map((event, index) => <li key={`${event.at}-${index}`}>{event.at} · {event.actor} · {event.action} · {event.reason}</li>)}</ul>
    </section>}
  </section>
}
