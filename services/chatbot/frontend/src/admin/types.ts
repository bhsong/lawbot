export type Label = 'like' | 'flag'
export type Status = 'pending' | 'reviewed' | 'hold' | 'rejected' | 'quarantined' | 'promoted'
export type Verdict = 'correct' | 'incorrect' | 'uncertain'
export type Decision = 'approved' | 'hold' | 'rejected'
export type GoldArticle = { law: string; article_no: string }
export type Article = GoldArticle & { title: string; kind: string }

type ReviewSummary = { verdict: Verdict; decision: Decision; reviewer: string; created_at: string }
export type FeedbackItem = {
  feedback_id: string
  label: Label
  status: Status
  question_preview: string
  reason: string | null
  ts: string
  latest_review: ReviewSummary | null
  warnings: string[]
}
export type FeedbackPage = { items: FeedbackItem[]; next_cursor: string | null }

export type Review = ReviewSummary & {
  review_id: number
  feedback_id: string
  scope: 'in' | 'out' | null
  gold_articles: GoldArticle[]
  corrected_question: string | null
  corrected_answer: string | null
  note: string | null
}
export type Promotion = {
  promotion_id: number
  feedback_id: string
  qa_uid: string
  approver: string
  created_at: string
  state: 'active' | 'revoked'
  revoke_note: string | null
}
export type FeedbackDetailData = {
  feedback: {
    feedback_id: string
    question: string
    answer_raw: string | null
    answer_shown: string | null
    hits?: { law?: string; article_id?: string; title?: string; doc?: string }[] | null
    citations?: { law: string; article_id: string; verdict: string }[] | null
  }
  reviews: Review[]
  promotions: Promotion[]
  checks: {
    overlap: { qa_uid: string; split_set: string; split: string; how: string | number } | null
    corpus: {
      feedback_dataset_version: string | null
      current_dataset_version: string | null
      feedback_articles_sha256: string | null
      current_articles_sha256: string | null
      changed_hits: { law: string; article_id: string; status: 'same' | 'changed' | 'missing' }[]
    }
  }
}
export type PromotionResult =
  | { feedback_id: string; ok: true; promotion_id: number; qa_uid: string }
  | { feedback_id: string; ok: false; code: string; detail: string }

export const statusLabels: Record<Status, string> = {
  pending: '검수 대기', reviewed: '검수 완료', hold: '보류', rejected: '반려', quarantined: '격리', promoted: '승격됨',
}
export const reasonLabels: Record<string, string> = {
  retrieval: '검색 오류', answer: '답변 오류', false_refusal: '불필요한 거절', other: '기타',
}
export const verdictLabels: Record<Verdict, string> = { correct: '정답', incorrect: '오답', uncertain: '판정 보류' }
export const decisionLabels: Record<Decision, string> = { approved: '승인', hold: '보류', rejected: '반려' }
