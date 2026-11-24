import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, Stop } from './icons'

const MAX = 2000 // same limit as the server (web.MAX_QUESTION)

/** Bottom input: Enter sends, Shift+Enter breaks the line, Korean IME composition is never sent half-done. */
export function Composer({ busy, onSend, onStop, consultTrace }: { busy: boolean; onSend: (q: string) => void; onStop: () => void; consultTrace: boolean }) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  function submit() {
    const q = text.trim()
    if (!q || busy) return
    onSend(q)
    setText('')
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <div className="box">
        <textarea
          ref={ref}
          rows={1}
          aria-label="질문"
          aria-describedby={consultTrace ? 'consult-retention' : undefined}
          placeholder="인사·근태·급여 관련 법을 질문하세요"
          maxLength={MAX}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
              e.preventDefault()
              submit()
            }
          }}
        />
        {busy ? (
          <button type="button" className="send" aria-label="중지" onClick={onStop}>
            <Stop />
          </button>
        ) : (
          <button type="submit" className="send" aria-label="보내기" disabled={!text.trim()}>
            <ArrowUp />
          </button>
        )}
      </div>
      {consultTrace && <p id="consult-retention" className="hint" data-testid="consult-retention">
        상담 내용은 디버깅·평가·학습을 위해 기한 없이 보관되며, 관리자에게 삭제를 요청할 수 있습니다.
      </p>}
      <p className="hint">법률 자문이 아닌 참고용 답변입니다. 중요한 결정은 전문가와 확인하세요.</p>
    </form>
  )
}
