import { useState } from 'react'
import type { CalendarChange, EventKind } from './types'
import { dateRoleLabel } from './types'

const labels = {
  notice: ['입법예고', '국회 예고 시작·종료일 및 게시일 기준', '이 날짜에 표시할 입법예고가 없습니다.'],
  effective: ['시행 조문', '해당 날짜에 효력이 시작되는 조문 기준', '이 날짜에 시행되는 조문이 없습니다.'],
  bill: ['법률안', '국회 의안의 발의·제안일 및 의결일 기준', '이 날짜에 표시할 법률안이 없습니다.'],
}

export function EventList({ kind, changes, onOpen }: {
  kind: EventKind; changes: CalendarChange[]; onOpen: (change: CalendarChange) => void
}) {
  const [limit, setLimit] = useState(8)
  const [title, description, empty] = labels[kind]
  const count = kind === 'effective' ? changes.length : new Set(changes.map(change => change.eventId ?? change.id)).size
  return <section className={`lc-list lc-${kind}`} aria-labelledby={`lc-${kind}-title`}>
    <div className="lc-list-head"><h3 id={`lc-${kind}-title`}>{title} <span>{count}건</span></h3>
      <p>{description}</p></div>
    {changes.length === 0 ? <p className="lc-empty">{empty}</p>
      : <ul>{changes.slice(0, limit).map((change) => <li key={change.id}>
        <button type="button" onClick={() => onOpen(change)}>
          <span className="lc-item-title">{change.lawName} <span>{change.article}</span></span>
          <span className="lc-summary">{change.assembly
            ? [dateRoleLabel(change.assembly.dateRole), change.assembly.agency].filter(Boolean).join(' · ')
            : change.summary}</span>
          <span className="lc-item-status">{change.status} <span aria-hidden="true">↗</span></span>
        </button>
      </li>)}</ul>}
    {changes.length > limit && <button className="lc-more" type="button" onClick={() => setLimit(limit + 8)}>
      {title} 더 보기 ({limit}/{changes.length})
    </button>}
  </section>
}
