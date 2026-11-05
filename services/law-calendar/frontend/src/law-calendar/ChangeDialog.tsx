import { useEffect, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import type { CalendarChange } from './types'
import { dateRoleLabel } from './types'

/** Preserve every code point; highlighting is display-only and never replaces source text. */
export function highlightedText(text: string, other: string, tag: 'ins' | 'del') {
  const chars = Array.from(text), reference = Array.from(other)
  let start = 0, end = chars.length, otherEnd = reference.length
  while (start < end && start < otherEnd && chars[start] === reference[start]) start++
  while (end > start && otherEnd > start && chars[end - 1] === reference[otherEnd - 1]) { end--; otherEnd-- }
  const Tag = tag
  return <>{chars.slice(0, start).join('')}<Tag>{chars.slice(start, end).join('')}</Tag>{chars.slice(end).join('')}</>
}

export function ChangeDialog({ change, demo, onClose }: { change: CalendarChange; demo: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    return () => { element.close(); previous?.focus() }
  }, [])

  const assembly = change.assembly
  const notice = change.kind === 'notice'
  const noticeContent = notice ? change.noticeContent : null
  const storedComparison = !change.comparable && (change.before !== null || change.after !== null)
  const highlight = change.comparable || (change.before !== null && change.after !== null)
  const metadata = assembly ? [
    ['의안번호', assembly.billNo], ['소관기관', assembly.agency],
    ...(change.kind === 'notice'
      ? [['예고 시작일', assembly.noticeStart], ['예고 종료일', assembly.noticeEnd]]
      : [['발의·제안일', assembly.billProposed], ['의결일', assembly.billDecided],
         ['소관위 처리일', assembly.committeeDecided], ['소관위 결과', assembly.committeeResult],
         ['법사위 처리일', assembly.judiciaryDecided], ['법사위 결과', assembly.judiciaryResult]]),
    ['수집 시각', change.collectedAt], ['검증 시각', change.verifiedAt],
  ] : [
    ['게시일', change.publishedOn], ['공포일', change.promulgatedOn],
    ['시행일', change.effectiveOn], ['의견제출 기한', change.commentDeadline],
    ['수집 시각', change.collectedAt], ['검증 시각', change.verifiedAt],
  ]
  function keepFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const elements = [...dialog.current!.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], summary, [tabindex="0"]')]
      .filter(element => element.getClientRects().length > 0)
    const first = elements[0], last = elements[elements.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
  return <dialog ref={dialog} className="lc-dialog" aria-labelledby="lc-detail-title" aria-describedby="lc-detail-notice"
    onKeyDown={keepFocus} onCancel={(event) => { event.preventDefault(); onClose() }}>
    <div className="lc-dialog-header">
      <span>{assembly ? dateRoleLabel(assembly.dateRole)
        : change.kind === 'notice' ? '입법예고 · 게시일 기준' : '시행 조문 · 시행일 기준'}</span>
      <button type="button" autoFocus aria-label="상세 닫기" onClick={onClose}>닫기 ×</button>
    </div>
    <div className="lc-dialog-body">
      {demo && <p className="lc-demo-badge">예시 / 실제 법령 아님</p>}
      <h2 id="lc-detail-title">{change.lawName} {change.article}</h2>
      <p className="lc-item-status">{change.status}</p>
      {change.summary && <p className="lc-detail-summary">{change.summary}</p>}
      <p id="lc-detail-notice">{assembly ? '열린국회정보와 국회 공식 원문에서 확인한 내용입니다.' : notice
        ? <>입법예고안은 확정된 법령이 아닙니다.{change.comparable && ' 게시 시점의 현행 조문과 개정안의 비교입니다.'}</>
        : <>{change.comparable && '시행 직전과 이후의 유효 조문 비교입니다. '}부칙·경과조치와 공식 원문을 함께 확인하세요.</>}</p>
      {noticeContent && <section className="lc-notice-content" aria-label="공식 개정안 내용">
        {noticeContent.summary && <><h3>주요 변경 내용</h3><p className="lc-notice-text">{noticeContent.summary}</p></>}
        {noticeContent.comparisonPages.length > 0 && <details className="lc-notice-comparison">
          <summary>공식 신·구조문대비표</summary>
          <div className="lc-notice-pages" role="region" aria-label="대비표 원문 페이지" tabIndex={0}>
            {noticeContent.comparisonPages.map(page => <img key={page} loading="lazy" decoding="async"
              src={`/api/law-changes/${encodeURIComponent(change.id)}/notice-comparison/${page}`}
              alt={`공식 신·구조문대비표 원문 ${page}쪽`} />)}
          </div>
        </details>}
        {noticeContent.pdfUrl?.startsWith('https://likms.assembly.go.kr/filegate/servlet/FileGate?') &&
          <p><a href={noticeContent.pdfUrl} target="_blank" rel="noopener noreferrer">법률안 PDF 원문 (새 창)</a></p>}
      </section>}
      {!assembly && <>
        {!change.comparable && <p className="lc-warning">{storedComparison
          ? <><strong>저장 원문 비교</strong> · <span>{notice ? '게시 시점 효력 미검증' : '시행 전후 효력 미검증'}</span></>
          : '비교 불가 · 저장된 원문이 없습니다.'}</p>}
        {(change.comparable || storedComparison) && <div className="lc-comparison" role="group"
          aria-label={storedComparison ? '저장 원문 비교' : '검증된 원문 비교'}>
          <section><h3>Before · {storedComparison ? '저장 구판' : notice ? '게시 시점 현행' : '시행 직전'}</h3>
            {change.before === null ? <p>{storedComparison ? '저장 구판 없음' : '신설 · 이전 조문 없음'}</p>
              : <pre>{highlight ? highlightedText(change.before, change.after ?? '', 'del') : change.before}</pre>}</section>
          <section><h3>After · {storedComparison ? '저장 신판' : notice ? '개정안' : '시행 이후'}</h3>
            {change.after === null ? <p>{storedComparison ? '저장 신판 없음' : '삭제 · 이후 조문 없음'}</p>
              : <pre>{highlight ? highlightedText(change.after, change.before ?? '', 'ins') : change.after}</pre>}</section>
        </div>}
      </>}
      <dl className="lc-metadata">{metadata.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? '확인되지 않음'}</dd></div>)}</dl>
      {change.sourceUrl && /^https:\/\//.test(change.sourceUrl)
        ? <a href={change.sourceUrl} target="_blank" rel="noopener noreferrer">공식 원문 확인 (새 창)</a>
        : <p className="lc-caption">{demo ? '합성 예시이므로 개별 공식 원문이 없습니다.' : '원문 링크 확인 필요'}</p>}
      {!assembly && <p><a href="https://www.law.go.kr/" target="_blank" rel="noopener noreferrer">국가법령정보센터 (새 창)</a></p>}
    </div>
  </dialog>
}
