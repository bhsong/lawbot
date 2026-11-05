import { useEffect, useRef, useState } from 'react'
import { ChangeDialog } from './ChangeDialog'
import { loadCalendar, loadChange } from './data'
import { dateLabel, todayKst } from './dates'
import { EventList } from './EventList'
import { HoldDialog } from './HoldDialog'
import { MonthCalendar } from './MonthCalendar'
import type { CalendarChange, CalendarLoader, CalendarSnapshot } from './types'
import './calendar.css'

const DISCLAIMER = '이 페이지의 법령 변경 정보와 요약은 참고용입니다. 입법예고안은 확정된 법령이 아닐 수 있으며, 실제 적용 전 반드시 국가법령정보센터 등 공식 원문과 시행일을 다시 확인하세요.'
type Scenario = 'normal' | 'loading' | 'error' | 'delayed'

export default function LawCalendarPage({ loader = loadCalendar, initialDate }: { loader?: CalendarLoader; initialDate?: string }) {
  const [selected, setSelected] = useState(initialDate ?? todayKst)
  const [snapshot, setSnapshot] = useState<CalendarSnapshot | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [scenario, setScenario] = useState<Scenario>('normal')
  const [opened, setOpened] = useState<CalendarChange | null>(null)
  const [holdsOpen, setHoldsOpen] = useState(false)
  const detailRequest = useRef<AbortController | null>(null)
  const [detailPending, setDetailPending] = useState(false)
  const [detailError, setDetailError] = useState(false)
  useEffect(() => () => detailRequest.current?.abort(), [])
  const month = selected.slice(0, 7)

  useEffect(() => {
    document.title = '법령 변경 캘린더 · Lawbot'
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setState('loading')
    setSnapshot(null)
    if (scenario === 'loading') return () => controller.abort()
    if (scenario === 'error') {
      setState('error')
      return () => controller.abort()
    }
    loader(month, controller.signal).then((data) => {
      if (controller.signal.aborted) return
      setSnapshot(data)
      setState('ready')
    }, () => {
      if (!controller.signal.aborted) setState('error')
    })
    return () => controller.abort()
  }, [month, loader, attempt, scenario])

  const changes = snapshot?.changes ?? []
  const notices = changes.filter((change) => change.date === selected && change.kind === 'notice')
  const noticeCount = new Set(notices.map(change => change.eventId ?? change.id)).size
  const effective = changes.filter((change) => change.date === selected && change.kind === 'effective')
  function select(day: string) {
    detailRequest.current?.abort()
    setDetailPending(false); setDetailError(false); setOpened(null); setSelected(day)
  }
  async function openChange(change: CalendarChange) {
    if (snapshot?.demo) { setOpened(change); return }
    detailRequest.current?.abort()
    const controller = new AbortController()
    detailRequest.current = controller
    setDetailPending(true); setDetailError(false)
    try {
      const detail = await loadChange(change.id, controller.signal)
      if (!controller.signal.aborted) setOpened(detail)
    } catch {
      if (!controller.signal.aborted) setDetailError(true)
    } finally {
      if (!controller.signal.aborted) setDetailPending(false)
    }
  }
  function retry() { setScenario('normal'); setAttempt((value) => value + 1) }

  return <main className="lc-page">
    <header className="lc-header">
      <div><p className="lc-eyebrow">LAWBOT / LAW CALENDAR</p><h1>법령 변경 캘린더</h1>
        <p className="lc-subtitle">게시된 예고와 시행되는 조문을 날짜별로 확인하세요.</p></div>
      <button type="button" className="lc-more" onClick={() => { detailRequest.current?.abort(); setDetailPending(false); setOpened(null); setHoldsOpen(true) }}>보류 목록</button>
    </header>
    <aside className="lc-disclaimer" aria-label="법령 정보 이용 안내">{DISCLAIMER}</aside>
    {import.meta.env.DEV && <details className="lc-preview"><summary>개발용 예시 화면 설정</summary>
      <p>실제 법령이 아닌 합성 데이터입니다. 오늘 날짜에 예시가 있으며 운영 빌드에는 포함되지 않습니다.</p>
      <label>화면 상태 <select value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}>
        <option value="normal">정상</option><option value="loading">로딩</option><option value="error">오류</option><option value="delayed">갱신 지연</option>
      </select></label>
    </details>}
    {snapshot?.demo && <p className="lc-demo-badge">예시 / 실제 법령 아님</p>}
    {state === 'ready' && !snapshot?.demo && changes.length === 0 && <p className="lc-warning">이 달에 공개 승인된 법령 변경 정보가 없습니다.</p>}
    <section aria-label="데이터 확인 상태" className="lc-data-status">
      <span>수집 시각: {snapshot?.collectedAt ?? '확인되지 않음'}</span>
      <span>검증 시각: {snapshot?.verifiedAt ?? '확인되지 않음'}</span>
      <span>날짜 기준: 한국 표준시(KST)</span>
    </section>
    {(snapshot?.delayed || scenario === 'delayed') && <p className="lc-warning" role="status">데이터 갱신이 지연되고 있습니다. 마지막 확인 시각과 공식 원문을 확인하세요.</p>}
    <MonthCalendar selected={selected} changes={changes} totals={snapshot?.counts} onSelect={select} />
    <section className="lc-results" aria-labelledby="lc-selected-date" aria-busy={state === 'loading'}>
      <div className="lc-selected"><h2 id="lc-selected-date">{dateLabel(selected)}</h2>
        {state === 'ready' && <p aria-live="polite">입법예고 {noticeCount}건 · 시행 조문 {effective.length}건</p>}</div>
      {state === 'loading' && <p className="lc-empty" role="status">법령 변경 정보를 불러오는 중입니다.</p>}
      {state === 'error' && <div className="lc-warning" role="alert"><p>법령 변경 정보를 불러오지 못했습니다.</p><button type="button" onClick={retry}>다시 시도</button></div>}
      {state === 'ready' && <div className="lc-panels">
        <EventList key={`notice-${selected}`} kind="notice" changes={notices} onOpen={openChange} />
        <EventList key={`effective-${selected}`} kind="effective" changes={effective} onOpen={openChange} />
      </div>}
    </section>
    {detailPending && <p role="status">상세 정보를 불러오는 중입니다.</p>}
    {detailError && <p className="lc-warning" role="alert">상세 정보를 확인하지 못했습니다. 항목을 다시 선택하세요.</p>}
    <footer className="lc-footer">
      <h2>공식 원문을 먼저 확인하세요</h2>
      <p>본 서비스는 공개된 법령·입법예고 자료를 수집·가공하여 제공하며, 수집 지연, 누락, 해석 또는 요약 오류가 발생할 수 있습니다. 법령의 적용 여부는 개별 조항의 시행일, 부칙, 경과조치 및 사실관계에 따라 달라질 수 있습니다. 중요한 법적 판단이나 조치를 하기 전 공식 법령·입법예고 원문을 직접 확인하고, 필요하면 전문가의 검토를 받으시기 바랍니다. 이 서비스는 개별 사건에 대한 법률 자문을 대신하지 않습니다.</p>
      <nav aria-label="공식 출처"><a href="https://www.law.go.kr/" target="_blank" rel="noopener noreferrer">국가법령정보센터 ↗</a>
        <a href="https://opinion.lawmaking.go.kr/" target="_blank" rel="noopener noreferrer">국민참여입법센터 ↗</a></nav>
    </footer>
    {opened && <ChangeDialog change={opened} demo={snapshot?.demo ?? false} onClose={() => setOpened(null)} />}
    {holdsOpen && <HoldDialog demo={snapshot?.demo ?? false} onClose={() => setHoldsOpen(false)} />}
  </main>
}
