import { useState } from 'react'
import type { CalendarChange } from './types'

export function EventList({ changes, onOpen }: {
  changes: CalendarChange[]; onOpen: (change: CalendarChange) => void
}) {
  const [limit, setLimit] = useState(8)
  return <section className="lc-list lc-effective" aria-labelledby="lc-effective-title">
    <div className="lc-list-head"><h3 id="lc-effective-title">시행 조문 <span>{changes.length}건</span></h3>
      <p>해당 날짜에 효력이 시작되는 조문 기준</p></div>
    {changes.length === 0 ? <p className="lc-empty">이 날짜에 시행되는 조문이 없습니다.</p>
      : <ul>{changes.slice(0, limit).map((change) => <li key={change.id}>
        <button type="button" onClick={() => onOpen(change)}>
          <span className="lc-item-title">{change.lawName} <span>{change.article}</span></span>
          <span className="lc-summary">{change.summary}</span>
          <span className="lc-item-status">{change.status} <span aria-hidden="true">↗</span></span>
        </button>
      </li>)}</ul>}
    {changes.length > limit && <button className="lc-more" type="button" onClick={() => setLimit(limit + 8)}>
      시행 조문 더 보기 ({limit}/{changes.length})
    </button>}
  </section>
}
