import type { FeedbackDetailData } from './types'
import { decisionLabels, verdictLabels } from './types'

const articleStatus = { same: '동일', changed: '변경', missing: '현재 조문 없음' }

export function FeedbackDetail({ detail }: { detail: FeedbackDetailData }) {
  const { feedback, reviews, promotions, checks } = detail
  const { corpus, overlap } = checks
  return <>
    <label>원 질문<textarea readOnly value={feedback.question} rows={2} /></label>
    <section aria-label="기존 답변">
      <h3>기존 답변 원문</h3>
      <p className="admin-original">{feedback.answer_raw || '저장된 원문이 없습니다.'}</p>
      {feedback.answer_shown && feedback.answer_shown !== feedback.answer_raw && <>
        <h3>화면에 표시된 답변</h3><p className="admin-original">{feedback.answer_shown}</p>
      </>}
    </section>
    <section aria-label="참고 조문과 버전">
      <h3>참고 조문</h3>
      <p>당시 버전 {corpus.feedback_dataset_version ?? '미기록'} · 현재 버전 {corpus.current_dataset_version ?? '미확인'}</p>
      {corpus.changed_hits.length > 0 && <ul>{corpus.changed_hits.map((hit, index) =>
        <li key={index}>{hit.law} {hit.article_id} · {articleStatus[hit.status]}</li>
      )}</ul>}
      {(feedback.hits ?? []).map((hit, index) => (
        <details key={index}><summary>{[hit.law, hit.article_id, hit.title].filter(Boolean).join(' ') || '당시 검색 조문'}</summary>
          <p className="admin-original">{hit.doc}</p>
        </details>
      ))}
      {overlap && <p>유사 문항 {overlap.qa_uid} · {overlap.split_set} / {overlap.split}</p>}
    </section>
    <section aria-label="인용 판정">
      <h3>인용 판정</h3>
      {feedback.citations?.length ? <ul>{feedback.citations.map((citation, index) =>
        <li key={index}>{citation.law} {citation.article_id} · {citation.verdict}</li>
      )}</ul> : <p>표시할 인용 판정이 없습니다.</p>}
    </section>
    <section aria-label="검수와 승격 이력">
      <h3>검수 이력</h3>
      {reviews.length ? <ul>{reviews.map((review) => <li key={review.review_id}>
        {review.feedback_id !== feedback.feedback_id && '이전 라벨의 검수 · '}
        {verdictLabels[review.verdict]} / {decisionLabels[review.decision]} · {review.reviewer} · {review.created_at}
        {review.note && <p className="admin-original">{review.note}</p>}
      </li>)}</ul> : <p>검수 이력이 없습니다.</p>}
      <h3>승격 이력</h3>
      {promotions.length ? <ul>{promotions.map((promotion) => <li key={promotion.promotion_id}>
        {promotion.state === 'active' ? '승격됨' : '취소됨'} · {promotion.qa_uid} · {promotion.approver} · {promotion.created_at}
        {promotion.revoke_note && <p className="admin-original">{promotion.revoke_note}</p>}
      </li>)}</ul> : <p>승격 이력이 없습니다.</p>}
    </section>
  </>
}
