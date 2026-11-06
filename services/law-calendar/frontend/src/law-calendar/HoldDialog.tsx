import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { loadHolds } from './data'
import type { HoldPage } from './api-types'
import type { HoldKind, HoldLoader } from './types'

export function HoldDialog({ demo, onClose, loader }: { demo: boolean; onClose: () => void; loader?: HoldLoader }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [kind, setKind] = useState<HoldKind>('effective')
  const [page, setPage] = useState(1)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<HoldPage | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    return () => { element.close(); previous?.focus() }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setResult(null); setError(false)
    const request = loader ? loader(page, controller.signal, kind) : loadHolds(page, controller.signal, kind, demo)
    request.then(data => { if (!controller.signal.aborted) setResult(data) },
      () => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [page, attempt, demo, kind, loader])
  function selectKind(next: HoldKind) {
    if (kind === next) return
    setResult(null); setError(false); setPage(1); setKind(next)
  }
  function keepFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const elements = dialog.current!.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]')
    const first = elements[0], last = elements[elements.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  return <dialog ref={dialog} className="lc-dialog" aria-labelledby="lc-holds-title" aria-describedby="lc-holds-notice"
    onKeyDown={keepFocus} onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="lc-dialog-header"><span>{kind === 'notice' ? '예고기간 확인 필요' : '시행일 확인 필요'}</span>
      <button type="button" autoFocus aria-label="보류 목록 닫기" onClick={onClose}>닫기 ×</button></div>
    <div className="lc-dialog-body">
      <h2 id="lc-holds-title">보류 목록</h2>
      {demo && <p className="lc-demo-badge">예시 / 실제 법령 아님</p>}
      <div role="group" aria-label="보류 유형" className="lc-hold-filters">
        <button type="button" className="lc-more" aria-pressed={kind === 'effective'} onClick={() => selectKind('effective')}>시행일 보류</button>
        <button type="button" className="lc-more" aria-pressed={kind === 'notice'} onClick={() => selectKind('notice')}>입법예고 검토 대기</button>
      </div>
      <p id="lc-holds-notice">{kind === 'notice' ? '공식 의안번호와 예고기간을 확인 중인 입법예고입니다.'
        : '시행일을 확정하지 못해 보류한 항목입니다.'} 선택한 날짜와 관계없이 표시하며 달력의 날짜별 건수에 포함하지 않습니다.</p>
      {!result && !error && <p role="status">보류 목록을 불러오는 중입니다.</p>}
      {error && <div role="alert" className="lc-warning"><p>보류 목록을 불러오지 못했습니다.</p>
        <button type="button" onClick={() => setAttempt(value => value + 1)}>다시 시도</button></div>}
      {result && <>
        <p aria-live="polite">총 {result.total}건 · {page}페이지</p>
        {result.total === 0 ? <p>{kind === 'notice' ? '현재 검토 대기 중인 입법예고가 없습니다.' : '현재 공개된 보류 항목이 없습니다.'}</p> : <ul className="lc-hold-list">
          {result.items.map(item => <li key={item.id}><h3>{item.lawName}</h3><p>{'billNo' in item ? `의안번호 ${item.billNo}` : item.article}</p>
            <p className="lc-warning">{item.reason}</p><p className="lc-caption">수집 시각: {item.collectedAt}</p>
            <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer">공식 원문 확인 (새 창)</a></li>)}
        </ul>}
      </>}
      <nav aria-label="보류 목록 페이지" className="lc-hold-pages">
        <button type="button" className="lc-more" disabled={!result || page === 1} onClick={() => setPage(value => value - 1)}>이전 보류 목록</button>
        <button type="button" className="lc-more" disabled={!result || page * result.page_size >= result.total} onClick={() => setPage(value => value + 1)}>다음 보류 목록</button>
      </nav>
    </div>
  </dialog>
}
