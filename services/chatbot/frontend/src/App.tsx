import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError, NETWORK_ERROR, api } from './api'
import { Composer } from './components/Composer'
import { Close, Logout } from './components/icons'
import { Login } from './components/Login'
import { Segmented } from './components/Segmented'
import { SourcesView } from './components/SourcesView'
import { TurnView } from './components/TurnView'
import type { Config, Feedback, Turn } from './types'
import { useChatStream } from './useChatStream'

export default function App() {
  const [state, setState] = useState<'checking' | 'out' | 'in'>('checking')
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.me().then(
      () => setState('in'),
      () => setState('out'),
    )
  }, [])

  useEffect(() => {
    if (state !== 'in') return
    api.config().then(
      (c) => {
        setConfig(c)
        document.title = c.title
      },
      (e) => (e instanceof ApiError && e.status === 401 ? setState('out') : setError(e instanceof ApiError ? e.message : NETWORK_ERROR)),
    )
  }, [state])

  const signedOut = useCallback(() => {
    setConfig(null)
    setState('out')
  }, [])

  if (state === 'out') return <Login onDone={() => setState('in')} />
  if (error) return <p className="error page-error" role="alert">{error}</p>
  if (state === 'checking' || !config) return null
  return <Chat config={config} onSignedOut={signedOut} />
}

function Chat({ config, onSignedOut }: { config: Config; onSignedOut: () => void }) {
  const first = config.models.find((m) => m.key === config.default_model) ?? config.models[0]
  const [model, setModel] = useState(first.key)
  const [format, setFormat] = useState(first.format)
  const [selected, setSelected] = useState<string | null>(null) // null: follow the latest answer
  const [sheet, setSheet] = useState<string | null>(null)
  const chat = useChatStream({ onUnauthorized: onSignedOut })
  const scroller = useRef<HTMLDivElement>(null)

  const shown = chat.turns.find((t) => t.id === selected) ?? chat.turns.at(-1)
  const sheetTurn = chat.turns.find((t) => t.id === sheet)

  useLayoutEffect(() => {
    const el = scroller.current
    if (el && selected === null) el.scrollTop = el.scrollHeight
  }, [chat.turns, selected])

  function send(q: string) {
    setSelected(null)
    chat.send(q, model, format)
  }

  async function feedback(t: Turn, fb: Feedback) {
    if (!t.turnId) return
    const prev = t.feedback ?? null
    chat.setFeedback(t.id, fb)
    try {
      await api.feedback(t.turnId, fb ? fb.label : 'cleared', fb?.label === 'flag' ? fb.reason : undefined)
    } catch {
      chat.setFeedback(t.id, prev)
    }
  }

  const modelItems = [
    ...config.models.map((m) => ({ value: m.key, label: m.label, title: m.info })),
    ...config.disabled.map((m) => ({ value: m.key, label: m.label, title: '이 기기에서 사용하지 않는 모델', disabled: true })),
  ]

  return (
    <div className="app">
      <main className="chat">
        <header className="topbar">
          <div className="brand">
            <h1>{config.title}</h1>
            <p className="notice">{config.notice}</p>
          </div>
          <div className="controls">
            <Segmented
              label="답변 모델"
              items={modelItems}
              value={model}
              disabled={chat.busy}
              onChange={(k) => {
                setModel(k)
                setFormat(config.models.find((m) => m.key === k)?.format ?? format)
              }}
            />
            <Segmented label="답변 형식" items={config.formats} value={format} disabled={chat.busy} onChange={setFormat} />
            <button
              type="button"
              className="icon"
              aria-label="로그아웃"
              title="로그아웃"
              onClick={() => api.logout().finally(onSignedOut)}
            >
              <Logout />
            </button>
          </div>
        </header>
        <div className="scroll" ref={scroller}>
          {chat.turns.length === 0 ? (
            <section className="welcome">
              <h2>인사·근태·급여 관련 법, 조문을 근거로 답해 드려요</h2>
              <p>{config.intro}</p>
              <div className="examples">
                {config.examples.map((q) => (
                  <button key={q} type="button" className="example" onClick={() => send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <div className={`thread${chat.turns.length > 1 ? ' multi' : ''}`}>
              {chat.turns.map((t) => (
                <TurnView
                  key={t.id}
                  turn={t}
                  selected={t.id === shown?.id}
                  reasons={config.feedback.enabled ? config.feedback.reasons : null}
                  onSelect={() => setSelected(t.id === chat.turns.at(-1)?.id ? null : t.id)}
                  onRetry={() => send(t.question)}
                  onFeedback={(fb) => feedback(t, fb)}
                  onShowSources={() => setSheet(t.id)}
                />
              ))}
            </div>
          )}
        </div>
        <Composer busy={chat.busy} onSend={send} onStop={chat.stop} hint="법률 자문이 아닌 참고용 답변입니다. 중요한 결정은 전문가와 확인하세요." />
      </main>
      <aside className="panel" aria-label="근거 조문">
        <SourcesView title={config.sources_title} sources={shown?.sources} />
      </aside>
      {sheetTurn && (
        <div className="sheet-backdrop" onClick={() => setSheet(null)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="근거 조문" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <span className="grip" aria-hidden="true" />
              <button type="button" className="icon" aria-label="닫기" onClick={() => setSheet(null)}>
                <Close />
              </button>
            </div>
            <SourcesView title={config.sources_title} sources={sheetTurn.sources} />
          </div>
        </div>
      )}
    </div>
  )
}
