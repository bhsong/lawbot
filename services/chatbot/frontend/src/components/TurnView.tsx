import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'
import type { Feedback, Option, Turn } from '../types'
import { CalculationCard } from './CalculationCard'
import { Book, Check, Copy, Flag, Retry, ThumbUp } from './icons'
import { countSources } from './SourcesView'

// cjk-friendly: lets **bold** end right before Korean particles, e.g. **수당**을 (CommonMark alone does not)
const PLUGINS = [remarkGfm, remarkCjkFriendly]

export function AnswerMarkdown({ text }: { text: string }) {
  return <Markdown remarkPlugins={PLUGINS}>{text}</Markdown>
}

type Props = {
  turn: Turn
  selected: boolean
  reasons: Option[] | null // null: feedback is off
  onSelect: () => void
  onRetry: () => void
  onFeedback: (fb: Feedback) => void
  onShowSources: () => void
}

/** One question and its answer. Clicking the answer shows its articles in the side panel. */
export function TurnView({ turn, selected, reasons, onSelect, onRetry, onFeedback, onShowSources }: Props) {
  const n = countSources(turn.sources)
  return (
    <article className={`turn${selected ? ' selected' : ''}`}>
      <div className="user-row">
        <div className="bubble" data-testid="question">
          {turn.question}
        </div>
      </div>
      {turn.calculation && <CalculationCard calculation={turn.calculation} check={turn.calculationCheck} />}
      <div className="bot" data-testid="answer" onClick={onSelect}>
        {turn.status && (
          <p className="status" role="status">
            <span className="dot" aria-hidden="true" />
            {turn.status}
          </p>
        )}
        {turn.text && (
          <div className="answer">
            <AnswerMarkdown text={turn.text} />
          </div>
        )}
        {turn.stopped && <p className="note">답변을 중지했습니다.</p>}
        {turn.error && (
          <p className="error" role="alert">
            {turn.error}
          </p>
        )}
        {turn.stats && (
          <p className="stats" data-testid="lawbot-stats">
            {turn.stats}
          </p>
        )}
        {turn.done && (
          <div className="actions" onClick={(e) => e.stopPropagation()}>
            {turn.text && !turn.error && <CopyButton text={turn.text} />}
            {reasons && turn.turnId && <FeedbackButtons value={turn.feedback ?? null} reasons={reasons} onChange={onFeedback} />}
            <button type="button" className="icon" aria-label="다시 묻기" title="다시 묻기" onClick={onRetry}>
              <Retry />
            </button>
            {n > 0 && (
              <button type="button" className="chip only-mobile" onClick={onShowSources}>
                <Book size={15} /> 근거 조문 {n}개
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="icon"
      aria-label={done ? '복사됨' : '답변 복사'}
      title="답변 복사"
      onClick={async () => {
        await navigator.clipboard?.writeText(text)
        setDone(true)
        setTimeout(() => setDone(false), 1500)
      }}
    >
      {done ? <Check /> : <Copy />}
    </button>
  )
}

function FeedbackButtons({ value, reasons, onChange }: { value: Feedback; reasons: Option[]; onChange: (fb: Feedback) => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => box.current?.contains(e.target as Node) || setOpen(false)
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])
  const liked = value?.label === 'like'
  const flagged = value?.label === 'flag' ? value.reason : undefined
  return (
    <span className="fb" ref={box}>
      <button type="button" className="icon" aria-label="좋아요" title="좋아요" aria-pressed={liked} onClick={() => onChange(liked ? null : { label: 'like' })}>
        <ThumbUp />
      </button>
      <button type="button" className="icon" aria-label="문제 알리기" title="문제 알리기" aria-pressed={!!flagged} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Flag />
      </button>
      {open && (
        <span className="menu" role="menu" aria-label="문제 유형">
          {reasons.map((r) => (
            <button
              key={r.value}
              type="button"
              role="menuitemradio"
              aria-checked={flagged === r.value}
              onClick={() => {
                setOpen(false)
                onChange(flagged === r.value ? null : { label: 'flag', reason: r.value })
              }}
            >
              {r.label}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}
