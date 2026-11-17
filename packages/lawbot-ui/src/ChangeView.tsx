import type { CalendarChange } from './types'

/** Preserve every code point; highlighting is display-only and never replaces source text. */
export function highlightedText(text: string, other: string, tag: 'ins' | 'del') {
  const chars = Array.from(text), reference = Array.from(other)
  let start = 0, end = chars.length, otherEnd = reference.length
  while (start < end && start < otherEnd && chars[start] === reference[start]) start++
  while (end > start && otherEnd > start && chars[end - 1] === reference[otherEnd - 1]) { end--; otherEnd-- }
  const Tag = tag
  return <>{chars.slice(0, start).join('')}<Tag>{chars.slice(start, end).join('')}</Tag>{chars.slice(end).join('')}</>
}

export function ChangeView({ change, demo, titleId, descriptionId }: {
  change: CalendarChange; demo: boolean; titleId: string; descriptionId: string
}) {
  const notice = change.kind === 'notice'
  const storedComparison = !change.comparable && (change.before !== null || change.after !== null)
  const highlight = change.comparable || (change.before !== null && change.after !== null)
  const metadata = [
    ['게시일', change.publishedOn], ['공포일', change.promulgatedOn],
    ['시행일', change.effectiveOn], ['의견제출 기한', change.commentDeadline],
    ['수집 시각', change.collectedAt], ['검증 시각', change.verifiedAt],
  ]
  return <div className="lc-dialog-body">
    {demo && <p className="lc-demo-badge">예시 / 실제 법령 아님</p>}
    <h2 id={titleId}>{change.lawName} {change.article}</h2>
    <p className="lc-item-status">{change.status}</p>
    {change.summary && <p className="lc-detail-summary">{change.summary}</p>}
    <p id={descriptionId}>{notice
      ? <>입법예고안은 확정된 법령이 아닙니다.{change.comparable && ' 게시 시점의 현행 조문과 개정안의 비교입니다.'}</>
      : <>{change.comparable && '시행 직전과 이후의 유효 조문 비교입니다. '}부칙·경과조치와 공식 원문을 함께 확인하세요.</>}</p>
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
    <dl className="lc-metadata">{metadata.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? '확인되지 않음'}</dd></div>)}</dl>
    {change.sourceUrl && /^https:\/\//.test(change.sourceUrl)
      ? <a href={change.sourceUrl} target="_blank" rel="noopener noreferrer">공식 원문 확인 (새 창)</a>
      : <p className="lc-caption">{demo ? '합성 예시이므로 개별 공식 원문이 없습니다.' : '원문 링크 확인 필요'}</p>}
    <p><a href="https://www.law.go.kr/" target="_blank" rel="noopener noreferrer">국가법령정보센터 (새 창)</a></p>
  </div>
}
