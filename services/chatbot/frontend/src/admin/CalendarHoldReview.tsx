import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { dateRoleLabel } from '@lawbot/ui'
import { AdminApiError, adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'

type ChangeReview = {
  id: string; effective_on: string; summary: string; evidence_url: string; comparison_verified: boolean
}

type EffectiveReview = {
  template: {
    event_id: string; snapshot_id: string; decision: 'approve'; note: string
    source_url: string | null; dates_verified: boolean; promulgated_on: string | null; changes: ChangeReview[]
  }
  evidence_url?: string | null
  changes: { id: string; article: string; before: string | null; after: string | null }[]
}

const decisionLabels = { approve: '승인', reject: '반려', withdraw: '철회' }
type NoticeDecision = keyof typeof decisionLabels
type NoticeReview = {
  template: { snapshot_id: string; decision: NoticeDecision; note: string }
  title: string; source_url: string; bill_no: string; dates: { role: string; date: string }[]
}

function SourceLink({ url, label }: { url: string | null | undefined; label: string }) {
  return url?.startsWith('https://') && <a href={url} target="_blank" rel="noopener noreferrer">{label} (새 창)</a>
}

function EffectiveFields({ data }: { data: EffectiveReview }) {
  const { template } = data
  return <>
    <p><SourceLink url={template.source_url} label="공식 원문 확인" /></p>
    {data.evidence_url !== template.source_url && data.evidence_url &&
      <p><SourceLink url={data.evidence_url} label="공식 근거 확인" /></p>}
    <div className="admin-review-fields">
      <label>공식 출처 URL<input name="source_url" type="url" defaultValue={template.source_url ?? ''} required /></label>
      <label>공포일<input name="promulgated_on" type="date" defaultValue={template.promulgated_on ?? ''} required /></label>
    </div>
    <label className="admin-review-check">
      <input type="checkbox" name="dates_verified" defaultChecked={template.dates_verified} required /> 공포일과 부칙을 확인했습니다
    </label>
    {template.changes.map((change, index) => {
      const original = data.changes.find((item) => item.id === change.id)
      return <fieldset key={change.id} className="admin-review-change">
        <legend>{original?.article ?? change.id}</legend>
        {original && <div className="lc-comparison" role="group" aria-label="저장 원문 비교">
          <section><h3>이전 원문</h3>{original.before === null ? <p>저장 구판 없음</p> : <pre>{original.before}</pre>}</section>
          <section><h3>이후 원문</h3>{original.after === null ? <p>저장 신판 없음</p> : <pre>{original.after}</pre>}</section>
        </div>}
        <div className="admin-review-fields">
          <label>시행일<input type="date" name={`effective_on.${index}`} defaultValue={change.effective_on} required /></label>
          <label>근거 URL<input type="url" name={`evidence_url.${index}`} defaultValue={change.evidence_url} required /></label>
        </div>
        <label>변경 요약<input name={`summary.${index}`} defaultValue={change.summary} maxLength={300} required /></label>
        <label className="admin-review-check">
          <input type="checkbox" name={`comparison_verified.${index}`} defaultChecked={change.comparison_verified} required /> 시행 전후 원문을 비교했습니다
        </label>
      </fieldset>
    })}
  </>
}

function HoldForm({ data, path, holdId, onReviewed, onUnauthorized, onReload }: {
  data: EffectiveReview | NoticeReview; path: string; holdId: string; onReviewed: (date?: string) => void
  onUnauthorized: () => void; onReload: () => void
}) {
  const [decision, setDecision] = useState(data.template.decision)
  const mutation = useAdminMutation(onUnauthorized)
  const alert = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (mutation.error) alert.current?.focus() }, [mutation.error])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '')
    let date: string | undefined
    let payload: Record<string, unknown>
    if ('changes' in data) {
      const changes = data.template.changes.map((change, index) => ({
        ...change, effective_on: value(`effective_on.${index}`), evidence_url: value(`evidence_url.${index}`),
        summary: value(`summary.${index}`), comparison_verified: form.has(`comparison_verified.${index}`),
      }))
      payload = {
        ...data.template, note: value('note'), source_url: value('source_url'),
        promulgated_on: value('promulgated_on'), dates_verified: form.has('dates_verified'), changes,
      }
      date = changes.find((change) => change.id === holdId)?.effective_on ?? changes[0]?.effective_on
    } else {
      payload = { ...data.template, note: value('note'), decision }
      if (decision === 'approve') date = data.dates.find((item) => item.role === 'notice_start')?.date ?? data.dates[0]?.date
    }
    const response = await mutation.run<{ ok: boolean }>(path, payload)
    if (response?.ok) onReviewed(date)
  }

  const failure = mutation.failure?.error
  const stale = failure instanceof AdminApiError && failure.status === 409
  return <form className="admin-editor admin-hold-review" onSubmit={submit} noValidate>
    <fieldset className="admin-edit-fields" disabled={mutation.busy}>
      {'changes' in data ? <EffectiveFields data={data} /> : <>
        <p><strong>{data.title}</strong> · 의안번호 {data.bill_no}</p>
        <p><SourceLink url={data.source_url} label="공식 원문 확인" /></p>
        <dl className="lc-metadata">
          {data.dates.map(({ role, date }) => <div key={role}><dt>{dateRoleLabel(role)}</dt><dd>{date}</dd></div>)}
          {data.dates.length === 0 && <div><dt>공식 일정</dt><dd>확인되지 않음</dd></div>}
        </dl>
        <label>검토 결정<select value={decision} onChange={(event) => setDecision(event.target.value as NoticeDecision)}>
          {Object.entries(decisionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
      </>}
      <label>검토 메모<textarea name="note" defaultValue={data.template.note} rows={2} required /></label>
      {mutation.error && <p ref={alert} tabIndex={-1} role="alert" className="admin-error">{mutation.error}</p>}
      <div className="admin-actions">
        {stale ? <button type="button" onClick={onReload}>최신 검토 자료 불러오기</button>
          : <button type="submit" className="admin-review-submit">{mutation.busy ? '저장 중…' : decisionLabels[decision]}</button>}
      </div>
    </fieldset>
  </form>
}

export function CalendarHoldReview({ kind, holdId, title, reason, onBack, onReviewed, onUnauthorized }: {
  kind: 'effective' | 'notice'; holdId: string; title: string; reason: string; onBack: () => void
  onReviewed: (date?: string) => void; onUnauthorized: () => void
}) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const path = `/calendar/law-holds/${kind}/${encodeURIComponent(holdId)}`
  const load = useCallback((signal: AbortSignal) => adminGet<EffectiveReview | NoticeReview>(path, signal), [path])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)

  return <section>
    <h3 ref={heading} tabIndex={-1}>{kind === 'effective' ? '시행일 보류 검토' : '입법예고 검토'}</h3>
    <button type="button" onClick={onBack}>보류 목록으로</button>
    <p><strong>{title}</strong> · {reason}</p>
    {error != null ? <p className="admin-error" role="alert">{adminMessage(error)}{' '}
      <button type="button" onClick={retry}>다시 시도</button>
    </p> : data ? <HoldForm data={data} path={path} holdId={holdId} onReviewed={onReviewed}
      onUnauthorized={onUnauthorized} onReload={retry} /> : <p role="status">검토 자료를 불러오는 중입니다.</p>}
  </section>
}
