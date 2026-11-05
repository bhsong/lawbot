import { useLayoutEffect, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { dateLabel, monthDays, shiftDay, shiftMonth, todayKst, weekday } from './dates'
import type { CalendarChange, CalendarSnapshot } from './types'

export function MonthCalendar({ selected, changes, onSelect, totals }: {
  selected: string; changes: CalendarChange[]; onSelect: (date: string) => void; totals?: CalendarSnapshot['counts']
}) {
  const month = selected.slice(0, 7)
  const days = monthDays(month)
  const [year, number] = month.split('-').map(Number)
  const focusDate = useRef(false)
  const grid = useRef<HTMLTableElement>(null)
  const today = todayKst()
  const counts = new Map<string, { notice: number; effective: number }>()
  for (const change of changes) {
    const count = counts.get(change.date) ?? { notice: 0, effective: 0 }
    count[change.kind]++
    counts.set(change.date, count)
  }
  if (totals) {
    counts.clear()
    for (const day of totals) counts.set(day.event_on, { notice: day.notice_count, effective: day.effective_count })
  }

  useLayoutEffect(() => {
    if (focusDate.current) {
      grid.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus()
      focusDate.current = false
    }
  }, [selected])

  function navigate(event: KeyboardEvent, day: string) {
    const offsets: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }
    let next: string
    if (event.key in offsets) next = shiftDay(day, offsets[event.key])
    else if (event.key === 'Home') next = shiftDay(day, -weekday(day))
    else if (event.key === 'End') next = shiftDay(day, 6 - weekday(day))
    else if (event.key === 'PageUp') next = shiftMonth(day, -1)
    else if (event.key === 'PageDown') next = shiftMonth(day, 1)
    else return
    event.preventDefault()
    focusDate.current = true
    onSelect(next)
  }

  return <section className="lc-month" aria-labelledby="lc-month-title">
    <div className="lc-month-bar">
      <h2 id="lc-month-title" aria-live="polite">{year}년 {number}월</h2>
      <div className="lc-month-actions">
        <button type="button" aria-label="이전 달" onClick={() => onSelect(shiftMonth(selected, -1))}>←</button>
        <button type="button" onClick={() => onSelect(today)}>오늘</button>
        <button type="button" aria-label="다음 달" onClick={() => onSelect(shiftMonth(selected, 1))}>→</button>
      </div>
    </div>
    <p className="lc-legend"><span className="lc-notice">● 입법예고 · 게시일</span><span className="lc-effective">● 시행 · 조문 시행일</span></p>
    <table ref={grid} role="grid" aria-labelledby="lc-month-title" aria-describedby="lc-key-help">
      <thead><tr>{['일', '월', '화', '수', '목', '금', '토'].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>{Array.from({ length: days.length / 7 }, (_, week) => <tr key={week}>
        {days.slice(week * 7, week * 7 + 7).map((day) => {
          const count = counts.get(day)
          return <td key={day} aria-selected={day === selected}>
            <button type="button" data-outside={!day.startsWith(month)} tabIndex={day === selected ? 0 : -1}
              aria-label={`${dateLabel(day)}, 입법예고 ${count?.notice ?? 0}건, 시행 ${count?.effective ?? 0}건`}
              aria-pressed={day === selected} aria-current={day === today ? 'date' : undefined}
              onClick={() => onSelect(day)} onKeyDown={(event) => navigate(event, day)}>
              <span className="lc-day-number">{Number(day.slice(-2))}</span>
              {day === today && <span className="lc-today">오늘</span>}
              {count && <span className="lc-day-counts">
                {count.notice > 0 && <span className="lc-notice"><span className="lc-count-label">예고 </span>{count.notice}</span>}
                {count.effective > 0 && <span className="lc-effective"><span className="lc-count-label">시행 </span>{count.effective}</span>}
              </span>}
            </button>
          </td>
        })}
      </tr>)}</tbody>
    </table>
    <p id="lc-key-help" className="lc-caption">방향키로 날짜 이동 · Home/End로 주의 시작/끝 · Page Up/Down으로 월 이동</p>
  </section>
}
