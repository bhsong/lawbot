import { useState } from 'react'
import type { CalendarChange, EventKind } from './types'

export function EventList({ kind, changes, onOpen }: {
  kind: EventKind; changes: CalendarChange[]; onOpen: (change: CalendarChange) => void
}) {
  const [limit, setLimit] = useState(8)
  const title = kind === 'notice' ? '입법예고' : '시행 조문'
  const count = kind === 'notice' ? new Set(changes.map(change => change.eventId ?? change.id)).size : changes.length
  return <section className={`lc-list lc-${kind}`} aria-labelledby={`lc-${kind}-title`}>
    <div className="lc-list-head"><h3 id={`lc-${kind}-title`}>{title} <span>{count}건</span></h3>
      <p>{kind === 'notice' ? '게시일 기준 · 확정된 법령이 아닙니다' : '해당 날짜에 효력이 시작되는 조문 기준'}</p></div>
    {changes.length === 0 ? <p className="lc-empty">{kind === 'notice'
      ? '이 날짜에 게시된 입법예고가 없습니다.' : '이 날짜에 시행되는 조문이 없습니다.'}</p>
      : <ul>{changes.slice(0, limit).map((change) => <li key={change.id}>
        <button type="button" onClick={() => onOpen(change)}>
          <span className="lc-item-title">{change.lawName} <span>{change.article}</span></span>
          <span className="lc-summary">{change.summary}</span>
          <span className="lc-item-status">{change.status} <span aria-hidden="true">↗</span></span>
        </button>
      </li>)}</ul>}
    {changes.length > limit && <button className="lc-more" type="button" onClick={() => setLimit(limit + 8)}>
      {title} 더 보기 ({limit}/{changes.length})
    </button>}
  </section>
}
