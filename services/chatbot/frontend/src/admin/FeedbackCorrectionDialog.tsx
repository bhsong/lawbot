import { useCallback, useId, useRef, useState } from 'react'
import { Modal } from '@lawbot/ui'
import { adminGet, adminMessage, useAdminMutation, useAdminRead } from './adminApi'
import { FeedbackDetail } from './FeedbackDetail'
import type { Article, Decision, FeedbackDetailData, FeedbackItem, GoldArticle, Status, Verdict } from './types'
import { verdictLabels } from './types'

function ArticlePicker({ gold, onChange, onUnauthorized }: {
  gold: GoldArticle[]; onChange: (next: GoldArticle[]) => void; onUnauthorized: () => void
}) {
  const [search, setSearch] = useState('')
  const q = search.trim()
  const load = useCallback((signal: AbortSignal) => q
    ? adminGet<{ items: Article[] }>(`/articles?${new URLSearchParams({ q })}`, signal)
    : Promise.resolve({ items: [] }), [q])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)

  return <section aria-label="근거 조문">
    <label>근거 조문 검색<input value={search} onChange={(event) => setSearch(event.target.value)} maxLength={100} /></label>
    {q && !data && !error && <p role="status">조문을 찾는 중입니다.</p>}
    {q && error != null && <p role="alert">{adminMessage(error)} <button type="button" onClick={retry}>다시 시도</button></p>}
    {q && data && (data.items.length ? <ul className="admin-options">{data.items.map((article) => (
      <li key={JSON.stringify([article.law, article.article_no])}>
        <button type="button" title={article.title}
          disabled={gold.length >= 10 || gold.some((entry) => entry.law === article.law && entry.article_no === article.article_no)}
          onClick={() => {
            onChange([...gold, { law: article.law, article_no: article.article_no }])
            setSearch('')
          }}>{article.law} {article.article_no}</button>
      </li>
    ))}</ul> : <p>일치하는 조문이 없습니다.</p>)}
    {gold.length > 0 && <ul aria-label="선택한 근거 조문" className="admin-options">{gold.map(({ law, article_no }, index) => (
      <li key={JSON.stringify([law, article_no])}>{law} {article_no}{' '}
        <button type="button" aria-label={`${law} ${article_no} 삭제`} onClick={() => onChange(gold.filter((_, i) => i !== index))}>삭제</button>
      </li>
    ))}</ul>}
  </section>
}

export function FeedbackCorrectionDialog({ item, detail, onClose, onSaved, onUnauthorized }: {
  item: FeedbackItem; detail: FeedbackDetailData; onClose: () => void
  onSaved: (message: string) => void; onUnauthorized: () => void
}) {
  const review = detail.reviews.find((row) => row.feedback_id === item.feedback_id)
  const [question, setQuestion] = useState(review?.corrected_question ?? '')
  const [answer, setAnswer] = useState(review?.corrected_answer ?? '')
  const [gold, setGold] = useState<GoldArticle[]>(review?.gold_articles ?? [])
  const [verdict, setVerdict] = useState<Verdict | ''>(review?.verdict ?? '')
  const [note, setNote] = useState(review?.note ?? '')
  const id = useId()
  const initialFocus = useRef<HTMLTextAreaElement>(null)
  const mutation = useAdminMutation(onUnauthorized)
  const flag = item.label === 'flag'
  const title = flag ? '문제 신고 교정' : '좋아요 검수'
  const path = `/feedbacks/${encodeURIComponent(item.feedback_id)}`
  const blocked = item.status === 'quarantined' ? '격리된 피드백은 승격할 수 없습니다.'
    : !question.trim() || !answer.trim() || !gold.length || !verdict || verdict === 'uncertain'
      ? '교정 질문, 교정 답변, 근거 조문과 정답·오답 판정을 입력하세요.' : ''

  async function save(decision: Decision) {
    const result = await mutation.run<{ status: Status }>(`${path}/review`, {
      verdict: verdict || 'uncertain', decision, gold_articles: gold, scope: review?.scope,
      corrected_question: question.trim() || undefined, corrected_answer: answer.trim() || undefined, note,
    })
    if (result) onSaved(decision === 'approved' ? '검수를 저장했습니다.' : decision === 'hold' ? '보류를 저장했습니다.' : '반려를 저장했습니다.')
  }

  async function promote() {
    if (blocked) return
    const result = await mutation.run<{ qa_uid: string }>(`${path}/promote-down`, {
      corrected_question: question.trim(), corrected_answer: answer.trim(), gold_articles: gold,
      review_verdict: verdict, review_note: note, scope: review?.scope,
    })
    if (result) onSaved('승격을 완료했습니다.')
  }

  function edit<T>(set: (value: T) => void, value: T) {
    mutation.invalidate()
    set(value)
  }

  return <Modal header="피드백 상세" closeLabel="피드백 상세 닫기" labelledBy={`${id}-title`} describedBy={`${id}-intro`}
    initialFocus={initialFocus} onClose={() => { if (!mutation.busy) onClose() }}>
    <div className="lc-dialog-body admin-editor">
      <h2 id={`${id}-title`}>{title}</h2>
      <p id={`${id}-intro`}>원 질문과 기존 답변을 확인하고 검수 내용을 입력하세요.</p>
      <FeedbackDetail detail={detail} />
      <fieldset disabled={mutation.busy} className="admin-edit-fields">
        <legend>검수 입력</legend>
        <label>교정 질문<textarea ref={initialFocus} rows={3} maxLength={2000} value={question}
          onChange={(event) => edit(setQuestion, event.target.value)} /></label>
        <label>교정 답변<textarea rows={5} maxLength={8000} value={answer}
          onChange={(event) => edit(setAnswer, event.target.value)} /></label>
        <button type="button" disabled={!detail.feedback.answer_raw}
          onClick={() => {
            edit(setAnswer, detail.feedback.answer_raw ?? '')
            setVerdict('')
          }}>기존 답변을 초안으로 복사</button>
        <ArticlePicker gold={gold} onChange={(value) => edit(setGold, value)} onUnauthorized={onUnauthorized} />
        <fieldset className="admin-radio">
          <legend>판정</legend>
          {(Object.entries(verdictLabels) as [Verdict, string][]).map(([value, label]) => <label key={value}>
            <input type="radio" name={`${id}-verdict`} checked={verdict === value} onChange={() => edit(setVerdict, value)} /> {label}
          </label>)}
        </fieldset>
        <label>검수 메모<textarea rows={2} maxLength={2000} value={note} onChange={(event) => edit(setNote, event.target.value)} /></label>
        <div className="admin-actions">
          <button type="button" disabled={!verdict || verdict === 'uncertain'} onClick={() => void save('approved')}>검수 저장</button>
          {flag && <button type="button" disabled={!!blocked} aria-describedby={blocked ? `${id}-blocked` : undefined}
            onClick={() => void promote()}>승격</button>}
          <button type="button" onClick={() => void save('hold')}>보류</button>
          <button type="button" onClick={() => void save('rejected')}>반려</button>
        </div>
      </fieldset>
      {flag && blocked && <p id={`${id}-blocked`}>{blocked}</p>}
      {mutation.busy && <p role="status">저장 중입니다.</p>}
      {mutation.error && <p role="alert" className="admin-error">{mutation.error}</p>}
    </div>
  </Modal>
}
