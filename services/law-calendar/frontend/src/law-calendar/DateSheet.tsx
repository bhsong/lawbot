import { useEffect, useLayoutEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode, RefObject } from 'react'
import { dateLabel } from '@lawbot/ui'
import type { CalendarChange } from '@lawbot/ui'
import type { PublicTaxSchedule } from './api-types'

export function DateSheet({ date, changes, taxItems, demo, calendar, status, limit, scrollTop, lastChangeId,
  detailStep, onLimitChange, onOpen, onClose }: {
  date: string; changes: CalendarChange[]; taxItems: PublicTaxSchedule['items']; demo: boolean
  calendar: RefObject<HTMLDivElement | null>; status: ReactNode
  limit: number; scrollTop: number; lastChangeId?: string; detailStep: boolean
  onLimitChange: (limit: number) => void
  onOpen: (change: CalendarChange, scrollTop: number) => void; onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const firstAdded = useRef<number | null>(null)
  const lastIndex = changes.findIndex(change => change.id === lastChangeId)
  const visible = Math.max(limit, lastIndex + 1)
  useLayoutEffect(() => {
    const element = dialog.current!
    const opener = calendar.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')
    element.showModal()
    return () => {
      element.close()
      opener?.focus({ preventScroll: true })
    }
  }, [calendar])

  useEffect(() => {
    if (lastIndex < 0) return
    list.current?.children.item(lastIndex)?.querySelector('button')?.focus({ preventScroll: true })
    dialog.current!.scrollTop = scrollTop
  }, [detailStep, lastChangeId, lastIndex, scrollTop])

  useLayoutEffect(() => {
    if (firstAdded.current === null) return
    list.current?.children.item(firstAdded.current)?.querySelector('button')?.focus()
    firstAdded.current = null
  }, [visible])

  function keepFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const controls = event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]')
    const first = controls[0], last = controls[controls.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }

  return <dialog ref={dialog} className="lc-dialog lc-sheet" aria-labelledby="lc-sheet-title"
    onKeyDown={keepFocus} onCancel={event => { event.preventDefault(); onClose() }}
    onClick={event => {
      if (event.target !== event.currentTarget) return
      const { top, bottom, left, right } = event.currentTarget.getBoundingClientRect()
      if (event.clientY < top || event.clientY >= bottom || event.clientX < left || event.clientX >= right) onClose()
    }}>
    <div className="lc-dialog-header lc-sheet-header">
      <h2 id="lc-sheet-title">{dateLabel(date)}</h2>
      <button type="button" autoFocus aria-label="날짜 내역 닫기" onClick={onClose}>닫기 ×</button>
      {status}
    </div>
    <div className="lc-sheet-body">
      {demo && <p className="lc-demo-badge">예시 / 실제 법령 아님</p>}
      {changes.length > 0 && <section className="lc-list lc-effective" aria-labelledby="lc-sheet-effective-title">
        <div className="lc-list-head"><h3 id="lc-sheet-effective-title">시행 조문 <span>{changes.length}건</span></h3></div>
        <ul ref={list}>{changes.slice(0, visible).map(change => <li key={change.id}>
          <button type="button" onClick={event => {
            event.currentTarget.focus({ preventScroll: true })
            onOpen(change, dialog.current!.scrollTop)
          }}>
            <span className="lc-item-title">{change.lawName} <span>{change.article}</span></span>
            <span className="lc-summary">{change.summary}</span>
            <span className="lc-item-status">{change.status} <span aria-hidden="true">↗</span></span>
          </button>
        </li>)}</ul>
        {changes.length > visible && <button className="lc-more" type="button" disabled={detailStep} onClick={() => {
          firstAdded.current = visible
          onLimitChange(visible + 8)
        }}>
          시행 조문 더 보기 ({visible}/{changes.length})
        </button>}
      </section>}
      {taxItems.length > 0 && <section className="lc-list lc-tax-list" aria-labelledby="lc-sheet-tax-title">
        <div className="lc-list-head"><h3 id="lc-sheet-tax-title" className="lc-tax">세무일정 {taxItems.length}건</h3></div>
        <ul>{taxItems.map(item => <li key={item.id}>
          <strong>{item.title}</strong><p>기한 {dateLabel(item.date)}{item.targetPeriod && ` · 대상 ${item.targetPeriod}`}</p>
          <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer">국세청 원문 ↗</a>
        </li>)}</ul>
      </section>}
    </div>
  </dialog>
}
