import { useCallback, useState } from 'react'
import type { FormEvent } from 'react'
import { adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'

type Status = 'ok' | 'error' | 'cancelled'
type Scope = { user_hash?: string; start?: string; end?: string }
type Filters = Scope & { status?: string }
type Summary = {
  id: string; created_at: string; ended_at: string; user_hash: string; role: 'user' | 'admin'; status: Status
  answer_verdict: string | null; question_preview: string
}
type Page = { items: Summary[]; next_cursor: string | null }
type Redactions = { redaction_count: number; redaction_reasons: Record<string, number> }
type Body = { encoding: 'utf-8' | 'base64'; data: string }
type Detail = {
  consult: Omit<Summary, 'question_preview'> & Redactions & {
    request_id: string; session_id: string; error_type: string | null
    delivery_status: 'not_prepared' | 'interrupted' | 'sent'
    retention_policy: string; app_commit: string | null; corpus_release_id: string | null
    writer_system_sha256: string | null; feedback_turn_id: string | null
  }
  steps: (Redactions & { seq: number; stage: string; started_at: string; ended_at: string })[]
  calls: (Redactions & {
    seq: number; purpose: string; method: string; path: string; model_id: string | null; model_revision: string | null
    status_code: number | null; started_at: string; first_byte_at: string | null; ended_at: string
    usage: Record<string, number> | null; error_type: string | null; response_decoded: number
    send_status: 'sent' | 'failed_before_response' | 'unknown'; response_streaming: number; has_response: number
  })[]
}
type Chunk = { seq: number; offset_ns: number; byte_offset: number; body: Body }
type BodyPage = { next_cursor: string | null } & ({ body: Body | null } | { items: Chunk[] })
type ReaderProps = { path: string; onUnauthorized: () => void }
type Counts = { consults: number; steps: number; calls: number }
type Preview = { counts: Counts; confirmation: string }

const statusLabels: Record<Status, string> = { ok: '정상', error: '오류', cancelled: '취소' }
const shortCounts = ({ consults, steps, calls }: Counts) => `상담 ${consults}건 · 단계 ${steps}건 · 호출 ${calls}건`

function scopeFromForm(form: HTMLFormElement): Scope {
  const data = new FormData(form)
  const user_hash = String(data.get('user_hash') ?? '').trim()
  const start = String(data.get('start') ?? '')
  const end = String(data.get('end') ?? '')
  return {
    ...(user_hash && { user_hash }),
    ...(start && { start: new Date(start).toISOString() }),
    ...(end && { end: new Date(end).toISOString() }),
  }
}

function ScopeFields() {
  return <>
    <label>사용자 해시<input name="user_hash" pattern="[a-f0-9]{16}" maxLength={16} /></label>
    <label>시작 시각<input name="start" type="datetime-local" /></label>
    <label>종료 시각 (제외)<input name="end" type="datetime-local" /></label>
  </>
}

function BodyText({ body, testId }: { body: Body; testId?: string }) {
  return <>{body.encoding === 'base64' && <span className="admin-meta">Base64</span>}
    <pre className="admin-original" data-testid={testId}>{body.data}</pre>
  </>
}

function ConsultText({ path, onUnauthorized }: ReaderProps) {
  const load = useCallback((signal: AbortSignal) =>
    adminGet<{ question: string; answer_shown: string | null }>(`${path}/text`, signal), [path])
  const { data, error } = useAdminRead(load, onUnauthorized)
  if (error) return <p role="alert">{adminMessage(error)}</p>
  if (!data) return <p role="status">질문과 최종 글을 불러오는 중…</p>
  return <>
    <h3>질문</h3><p className="admin-original">{data.question}</p>
    <h3>최종 글</h3><p className="admin-original" data-testid="consult-shown">{data.answer_shown ?? '준비되지 않음'}</p>
  </>
}

function StepResult({ path, onUnauthorized }: ReaderProps) {
  const load = useCallback((signal: AbortSignal) => adminGet<{ result: unknown }>(path, signal), [path])
  const { data, error } = useAdminRead(load, onUnauthorized)
  if (error) return <p role="alert">{adminMessage(error)}</p>
  return data ? <pre className="admin-original">{JSON.stringify(data.result, null, 2)}</pre> :
    <p role="status">단계 결과를 불러오는 중…</p>
}

function CallBody({ path, part, onUnauthorized }: ReaderProps & { part: 'request' | 'response' | 'chunks' }) {
  const [cursor, setCursor] = useState('')
  const load = useCallback((signal: AbortSignal) => {
    const params = new URLSearchParams({ part, ...(cursor && { cursor }) })
    return adminGet<BodyPage>(`${path}/body?${params}`, signal)
  }, [cursor, part, path])
  const { data, error } = useAdminRead(load, onUnauthorized)
  if (error) return <p role="alert">{adminMessage(error)}</p>
  if (!data) return <p role="status">본문을 불러오는 중…</p>
  return <>
    {'items' in data ? (data.items.length ? <ol aria-label="수신 조각">{data.items.map((chunk) => (
      <li key={`${chunk.seq}:${chunk.byte_offset}`}>
        <span className="admin-meta">#{chunk.seq} · +{(chunk.offset_ns / 1e6).toFixed(3)} ms · {chunk.byte_offset} 바이트</span>
        <BodyText body={chunk.body} testId="consult-response-chunk" />
      </li>
    ))}</ol> : <p>수신 조각 없음</p>) : data.body &&
      <BodyText body={data.body} testId={`consult-${part}-body`} />}
    {cursor && <button type="button" onClick={() => setCursor('')}>본문 처음</button>}
    {data.next_cursor && <button type="button" onClick={() => setCursor(data.next_cursor ?? '')}>다음 본문</button>}
  </>
}

function StepEntry({ step, path, onUnauthorized }: ReaderProps & { step: Detail['steps'][number] }) {
  const [open, setOpen] = useState(false)
  return <li data-testid="consult-step">
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{step.stage}</summary><p className="admin-meta">{step.started_at} — {step.ended_at}</p>
      {open && <StepResult path={`${path}/steps/${step.seq}`} onUnauthorized={onUnauthorized} />}
      {step.redaction_count > 0 && <p className="admin-meta">가림: {JSON.stringify(step.redaction_reasons)}</p>}
    </details>
  </li>
}

function CallEntry({ call, path, onUnauthorized }: ReaderProps & { call: Detail['calls'][number] }) {
  const [open, setOpen] = useState(false)
  const url = `${path}/calls/${call.seq}`
  return <li data-testid="consult-call">
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{call.purpose} · {call.model_id} · {call.status_code ?? call.error_type}</summary>
      <p className="admin-meta">{call.method} {call.path} · revision {call.model_revision ?? '—'}</p>
      <p className="admin-meta">시작 {call.started_at} · 첫 바이트 {call.first_byte_at ?? '—'} · 종료 {call.ended_at}</p>
      {call.usage && <p className="admin-meta">토큰: {JSON.stringify(call.usage)}</p>}
      {call.error_type && <p className="admin-meta">오류: {call.error_type}</p>}
      {call.redaction_count > 0 && <p className="admin-meta">가림: {JSON.stringify(call.redaction_reasons)}</p>}
      {Boolean(call.response_decoded) && <p className="admin-meta">가림을 위해 압축을 해제했거나 본문을 저장하지 않았습니다.</p>}
      {open && <>
        <section aria-label="송신 본문">
          <h4>{call.send_status === 'sent' ? '송신 본문 (messages 포함)' : '시도 본문 (송신 미확인)'}</h4>
          <CallBody path={url} part="request" onUnauthorized={onUnauthorized} />
        </section>
        <section aria-label="수신 원문">
          <h4>수신 원문</h4>
          {call.response_streaming || call.has_response ?
            <CallBody path={url} part={call.response_streaming ? 'chunks' : 'response'} onUnauthorized={onUnauthorized} /> :
            <p>수신 본문 없음</p>}
        </section>
      </>}
    </details>
  </li>
}

function ConsultDetail({ id, onClose, onUnauthorized }: { id: string; onClose: () => void; onUnauthorized: () => void }) {
  const path = `/consults/${encodeURIComponent(id)}`
  const load = useCallback((signal: AbortSignal) => adminGet<Detail>(path, signal), [path])
  const { data, error } = useAdminRead(load, onUnauthorized)
  return <section className="admin-card" aria-label="상담 상세">
    <div className="admin-row"><h2>상담 상세</h2><button type="button" onClick={onClose}>상세 닫기</button></div>
    {error ? <p role="alert">{adminMessage(error)}</p> : !data ? <p role="status">상담 기록을 불러오는 중…</p> : <>
      <ConsultText path={path} onUnauthorized={onUnauthorized} />
      {data.consult.delivery_status === 'interrupted' && <p>최종 이벤트 전송이 중단되었습니다.</p>}
      <details><summary>상담 정보</summary><pre className="admin-original">{JSON.stringify({
        id, request_id: data.consult.request_id, created_at: data.consult.created_at, ended_at: data.consult.ended_at,
        user_hash: data.consult.user_hash, role: data.consult.role, session_id: data.consult.session_id,
        status: data.consult.status, answer_verdict: data.consult.answer_verdict, error_type: data.consult.error_type,
        delivery_status: data.consult.delivery_status,
        retention_policy: data.consult.retention_policy, app_commit: data.consult.app_commit,
        corpus_release_id: data.consult.corpus_release_id, writer_system_sha256: data.consult.writer_system_sha256,
        feedback_turn_id: data.consult.feedback_turn_id, redaction_reasons: data.consult.redaction_reasons,
      }, null, 2)}</pre></details>
      <h3>처리 단계</h3>
      <ol aria-label="처리 단계">{data.steps.map((step) =>
        <StepEntry key={step.seq} step={step} path={path} onUnauthorized={onUnauthorized} />)}</ol>
      <h3>모델 호출</h3>
      <ol aria-label="모델 호출">{data.calls.map((call) =>
        <CallEntry key={call.seq} call={call} path={path} onUnauthorized={onUnauthorized} />)}</ol>
    </>}
  </section>
}

function ConsultDelete({ onDeleted, onUnauthorized }: { onDeleted: () => void; onUnauthorized: () => void }) {
  const mutation = useAdminMutation(onUnauthorized)
  const [preview, setPreview] = useState<(Preview & { scope: Scope }) | null>(null)
  const [deleted, setDeleted] = useState<Counts | null>(null)
  const [error, setError] = useState('')

  async function previewDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const scope = scopeFromForm(event.currentTarget)
    setPreview(null)
    setDeleted(null)
    setError('')
    if (!Object.keys(scope).length) {
      setError('사용자 또는 기간을 지정하세요.')
      return
    }
    const result = await mutation.run<Preview>('/consults/delete-preview', scope)
    if (result) setPreview({ ...result, scope })
  }

  async function confirmDelete() {
    if (!preview) return
    const result = await mutation.run<Counts>('/consults/delete', { ...preview.scope, confirmation: preview.confirmation })
    if (result) {
      setPreview(null)
      setDeleted(result)
      onDeleted()
    }
  }

  return <section className="admin-card" aria-label="상담 삭제">
    <h2>상담 삭제</h2>
    <form onSubmit={previewDelete} onChange={() => { setPreview(null); setDeleted(null); setError(''); mutation.invalidate() }}>
      <fieldset className="admin-filter-fields" disabled={mutation.busy}>
        <legend>삭제 조건</legend><ScopeFields /><button type="submit">삭제 건수 미리 보기</button>
      </fieldset>
    </form>
    {preview && <div>
      <p role="status">삭제 대상: {shortCounts(preview.counts)}</p>
      <button type="button" disabled={mutation.busy || preview.counts.consults === 0} onClick={confirmDelete}>확인 후 삭제</button>
    </div>}
    {(error || mutation.error) && <p role="alert" className="admin-error">{error || mutation.error}</p>}
    {deleted && <p role="status" data-testid="consult-delete-result">{shortCounts(deleted)}을 삭제했습니다.</p>}
  </section>
}

export function ConsultPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [query, setQuery] = useState<{ filters: Filters; cursor?: string }>({ filters: {} })
  const [selected, setSelected] = useState<string | null>(null)
  const load = useCallback((signal: AbortSignal) => {
    const params = new URLSearchParams({ ...query.filters, ...(query.cursor && { cursor: query.cursor }) })
    return adminGet<Page>(`/consults?${params}`, signal)
  }, [query])
  const { data, error } = useAdminRead(load, onUnauthorized)

  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const status = String(new FormData(event.currentTarget).get('status') ?? '')
    setQuery({ filters: { ...scopeFromForm(event.currentTarget), ...(status && { status }) } })
    setSelected(null)
  }

  return <main className="lc-page admin-page admin-consults">
    <h1>상담 기록</h1>
    <section className="admin-card" aria-label="상담 조회">
      <h2>상담 목록</h2>
      <form onSubmit={filter} className="admin-filters">
        <fieldset className="admin-filter-fields">
          <legend>조회 조건</legend><ScopeFields />
          <label>상태<select name="status"><option value="">전체</option>{Object.entries(statusLabels).map(([value, label]) => (
            <option value={value} key={value}>{label}</option>
          ))}</select></label>
          <button type="submit">조회</button>
        </fieldset>
      </form>
      {error ? <p role="alert" className="admin-error">{adminMessage(error)}</p> : !data ? <p role="status">상담 목록을 불러오는 중…</p> : <>
        {data.items.length === 0 && <p role="status">상담 기록이 없습니다.</p>}
        <ul className="admin-feedback-items" aria-label="상담 목록">{data.items.map((item) => <li key={item.id} data-testid="consult-list-item">
          <button type="button" className="admin-question" onClick={() => setSelected(item.id)}>{item.question_preview}</button>
          <p className="admin-meta">{new Date(item.created_at).toLocaleString('ko-KR')} · {item.user_hash} · {item.role} · {statusLabels[item.status]}</p>
        </li>)}</ul>
        {query.cursor && <button type="button" onClick={() => setQuery({ filters: query.filters })}>첫 페이지</button>}
        {data.next_cursor && <button type="button" onClick={() => setQuery({ filters: query.filters, cursor: data.next_cursor! })}>다음 페이지</button>}
      </>}
    </section>
    {selected && <ConsultDetail key={selected} id={selected} onClose={() => setSelected(null)} onUnauthorized={onUnauthorized} />}
    <ConsultDelete onUnauthorized={onUnauthorized} onDeleted={() => {
      setSelected(null)
      setQuery({ filters: query.filters })
    }} />
  </main>
}
