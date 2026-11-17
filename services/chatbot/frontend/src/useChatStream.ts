import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, NETWORK_ERROR, api } from './api'
import { applyEvent, newTurn } from './turns'
import type { Feedback, Turn } from './types'

const CUT_OFF = '답변 도중 연결이 끊겼습니다. 다시 질문해 주세요.'

/** Chat state for one page: turns on screen only (nothing is saved), one question at a time. */
export function useChatStream({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => () => {
    const active = abort.current
    abort.current = null
    active?.abort()
  }, [])

  const update = useCallback((id: string, fn: (t: Turn) => Turn) => {
    setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))
  }, [])

  const send = useCallback(
    async (question: string, model: string, format: string) => {
      if (abort.current) return
      const turn = newTurn(question, model, format)
      const ctrl = new AbortController()
      abort.current = ctrl
      setTurns((all) => [...all, turn])
      setBusy(true)
      try {
        await api.chat({ question, model, format }, ctrl.signal, (e, d) => {
          if (!ctrl.signal.aborted) update(turn.id, (t) => applyEvent(t, e, d))
        })
        ctrl.signal.throwIfAborted()
        update(turn.id, (t) => (t.done ? t : { ...t, done: true, status: undefined, error: CUT_OFF }))
      } catch (err) {
        if (abort.current !== ctrl) return
        if (ctrl.signal.aborted) {
          update(turn.id, (t) => (t.done ? t : { ...t, done: true, stopped: true, status: undefined }))
        } else if (err instanceof ApiError && err.status === 401) {
          setTurns((all) => all.filter((t) => t.id !== turn.id))
          onUnauthorized()
        } else {
          const message = err instanceof ApiError ? err.message : NETWORK_ERROR
          update(turn.id, (t) => ({ ...t, done: true, status: undefined, error: message }))
        }
      } finally {
        if (abort.current === ctrl) {
          abort.current = null
          setBusy(false)
        }
      }
    },
    [onUnauthorized, update],
  )

  const stop = useCallback(() => abort.current?.abort(), [])

  const setFeedback = useCallback((id: string, feedback: Feedback) => update(id, (t) => ({ ...t, feedback })), [update])

  return { turns, busy, send, stop, setFeedback }
}
