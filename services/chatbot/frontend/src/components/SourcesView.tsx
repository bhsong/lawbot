import type { Sources } from '../types'

export function countSources(s?: Sources) {
  return s ? s.groups.reduce((n, g) => n + g.items.length, 0) : 0
}

/** Article cards: cited ones open, retrieved-only ones folded (grouping decided by the server). */
export function SourcesView({ title, sources }: { title: string; sources?: Sources }) {
  return (
    <div className="sources" data-testid="lawbot-sources">
      <h2>{title}</h2>
      {!sources ? (
        <p className="muted">질문하면 답변의 근거가 된 조문이 여기에 표시됩니다.</p>
      ) : (
        sources.groups.map((g, gi) => (
          <section key={`${gi}-${g.label}`} aria-label={g.label ?? '검색된 조문'}>
            {g.label && <h3>{g.label}</h3>}
            {g.items.map((it) => (
              <details key={`${g.label}-${it.title}`} className="card" open={g.open} data-testid="source-card">
                <summary>
                  <span className="card-title">{it.title}</span>
                  <span className="scores">{it.scores}</span>
                </summary>
                <p className="body">{it.body}</p>
              </details>
            ))}
          </section>
        ))
      )}
    </div>
  )
}
