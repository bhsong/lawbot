import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { dateLabel, EventList, MonthCalendar, todayKst } from '@lawbot/ui'
import type { CalendarChange } from '@lawbot/ui'
import { ChangeDialog } from './ChangeDialog'
import { DateSheet } from './DateSheet'
import { LAW_SOURCE, LICENSE_PATH } from '../attribution'
import { isDemoMode } from '../site-data'
import { loadCalendarWithTax, loadChange } from './data'
import { HoldDialog } from './HoldDialog'
import type { CalendarLoader, CalendarSnapshot } from './types'
import '@lawbot/ui/calendar.css'
import './calendar.css'

const DISCLAIMER = '이 페이지의 법령 변경 정보와 요약은 참고용입니다. 실제 적용 전 반드시 국가법령정보센터 등 공식 원문과 조문별 시행일을 다시 확인하세요.'
const MOBILE = '(max-width: 700px)'
type Scenario = 'normal' | 'loading' | 'error' | 'delayed'
type CalendarLayer = { owner: string; date: string; sheet: boolean; changeId?: string }
type MonthResult = { month: string; snapshot: CalendarSnapshot | null }

// An invalid detail owned by a sheet sits two entries above the inline calendar.
function skipLayer(layer: CalendarLayer) {
  history.go(layer.sheet && layer.changeId ? -2 : -1)
}

export default function LawCalendarPage({ loader = loadCalendarWithTax, initialDate }: { loader?: CalendarLoader; initialDate?: string }) {
  const [selected, setSelected] = useState(initialDate ?? todayKst)
  const [result, setResult] = useState<MonthResult | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [scenario, setScenario] = useState<Scenario>('normal')
  const [opened, setOpened] = useState<CalendarChange | null>(null)
  const [holdsOpen, setHoldsOpen] = useState(false)
  const [owner] = useState(() => crypto.randomUUID())
  const [layer, setLayer] = useState<CalendarLayer | null>(null)
  const calendar = useRef<HTMLDivElement>(null)
  const dateClick = useRef<HTMLButtonElement | null>(null)
  const checkedHistory = useRef(false)
  const detailRequest = useRef<AbortController | null>(null)
  const [detailPending, setDetailPending] = useState(false)
  const [detailError, setDetailError] = useState(false)
  useEffect(() => () => detailRequest.current?.abort(), [])
  const month = selected.slice(0, 7)
  const monthResult = result?.month === month ? result : null
  const snapshot = monthResult?.snapshot
  const state = !monthResult ? 'loading' : snapshot ? 'ready' : 'error'

  useEffect(() => {
    document.title = '법령 변경 캘린더 · Lawbot'
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setResult(null)
    if (scenario === 'loading') return () => controller.abort()
    if (scenario === 'error') {
      setResult({ month, snapshot: null })
      return () => controller.abort()
    }
    loader(month, controller.signal).then((data) => {
      if (controller.signal.aborted) return
      setResult({ month, snapshot: data })
    }, () => {
      if (!controller.signal.aborted) setResult({ month, snapshot: null })
    })
    return () => controller.abort()
  }, [month, loader, attempt, scenario])

  const changes = snapshot?.changes ?? []
  const effective = changes.filter((change) => change.date === selected)
  const taxData = snapshot?.tax
  const taxCounts = new Map<string, number>()
  for (const item of taxData?.items ?? []) taxCounts.set(item.date, (taxCounts.get(item.date) ?? 0) + 1)
  const taxItems = taxData?.items.filter(item => item.date === selected) ?? []
  const sheetOpen = layer?.sheet && state === 'ready' && (effective.length > 0 || taxItems.length > 0)
  const detailStatus = detailPending ? <p role="status">상세 정보를 불러오는 중입니다.</p>
    : detailError ? <p className="lc-warning" role="alert">상세 정보를 확인하지 못했습니다. 항목을 다시 선택하세요.</p> : null

  useEffect(() => {
    function follow(next: CalendarLayer | null) {
      if (location.hash === '#erp') return
      detailRequest.current?.abort()
      setDetailPending(false)
      setOpened(null)
      if (!next) setDetailError(false)
      if (next && (next.owner !== owner || !window.matchMedia?.(MOBILE).matches)) {
        skipLayer(next)
        return
      }
      setLayer(next)
      if (next) setSelected(next.date)
    }
    const onPop = (event: PopStateEvent) => follow(event.state?.lawCalendar ?? null)
    window.addEventListener('popstate', onPop)
    if (!checkedHistory.current) {
      checkedHistory.current = true
      if (history.state?.lawCalendar) follow(history.state.lawCalendar)
    }
    return () => window.removeEventListener('popstate', onPop)
  }, [owner])

  const restoreLayer = useEffectEvent(() => {
    if (!layer || layer.date !== selected || state === 'loading') return
    if (state === 'error' || (layer.sheet && !sheetOpen)) { skipLayer(layer); return }
    if (!layer.changeId || opened) return
    const change = effective.find(item => item.id === layer.changeId)
    if (change) openChange(change, true)
    else history.back()
  })
  useEffect(() => { restoreLayer() }, [layer, monthResult, opened, selected])

  function pushLayer(day: string, sheet: boolean, changeId?: string) {
    const next = { owner, date: day, sheet, changeId }
    history.pushState({ ...history.state, lawCalendar: next }, '')
    setLayer(next)
  }

  function select(day: string) {
    const clicked = dateClick.current
    dateClick.current = null
    detailRequest.current?.abort()
    setDetailPending(false); setDetailError(false); setOpened(null); setSelected(day)
    if (layer) { skipLayer(layer); return }
    if (clicked && state === 'ready' && window.matchMedia?.(MOBILE).matches
      && (changes.some(change => change.date === day) || taxCounts.has(day))) {
      pushLayer(day, true)
    }
  }

  function openChange(change: CalendarChange, restoring = false) {
    if (!restoring && layer?.changeId) return
    const sheet = !!layer?.sheet
    const record = !restoring && (sheet || window.matchMedia?.(MOBILE).matches)
    const show = (detail: CalendarChange) => {
      if (record) pushLayer(change.date, sheet, change.id)
      setOpened(detail)
    }
    detailRequest.current?.abort()
    setDetailError(false)
    if (snapshot?.demo) { show(change); return }
    const controller = new AbortController()
    detailRequest.current = controller
    setDetailPending(true)
    loadChange(change.id, controller.signal).then(detail => {
      if (controller.signal.aborted) return
      setDetailPending(false)
      show(detail)
    }, () => {
      if (controller.signal.aborted) return
      setDetailPending(false)
      setDetailError(true)
      if (restoring) history.back()
    })
  }
  function retry() { setScenario('normal'); setAttempt((value) => value + 1) }

  return <main className="lc-page">
    <header className="lc-header">
      <div><p className="lc-eyebrow">LAWBOT / LAW CALENDAR</p><h1>법령 변경 캘린더</h1>
        <p className="lc-subtitle">시행되는 조문을 날짜별로 확인하세요.</p></div>
      <button type="button" className="lc-more" onClick={() => { detailRequest.current?.abort(); setDetailPending(false); setOpened(null); setHoldsOpen(true) }}>보류 목록</button>
    </header>
    <aside className="lc-disclaimer" aria-label="법령 정보 이용 안내">{DISCLAIMER}</aside>
    {import.meta.env.DEV && isDemoMode() && <details className="lc-preview"><summary>개발용 예시 화면 설정</summary>
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
    {taxData === null && <p className="lc-warning" role="status">국세청 세무일정을 불러오지 못했습니다.</p>}
    {taxData?.confirmedAt && <p className="lc-caption">국세청 세무일정 확인 {taxData.confirmedAt}</p>}
    <div ref={calendar} onClickCapture={event => {
      // MonthCalendar emits the same selection callback for click and keyboard navigation.
      dateClick.current = event.target instanceof Element
        ? event.target.closest<HTMLButtonElement>('button[data-outside="false"]') : null
    }}>
      <MonthCalendar selected={selected} changes={changes} totals={snapshot?.counts} onSelect={select} taxCounts={taxData?.confirmedAt ? taxCounts : undefined} />
    </div>
    <section className="lc-results" aria-labelledby="lc-selected-date" aria-busy={state === 'loading'}>
      <div className="lc-selected"><h2 id="lc-selected-date">{dateLabel(selected)}</h2>
        {state === 'ready' && <p aria-live="polite">시행 조문 {effective.length}건</p>}</div>
      {state === 'loading' && <p className="lc-empty" role="status">법령 변경 정보를 불러오는 중입니다.</p>}
      {state === 'error' && <div className="lc-warning" role="alert"><p>법령 변경 정보를 불러오지 못했습니다.</p><button type="button" onClick={retry}>다시 시도</button></div>}
      {state === 'ready' && <div className="lc-panels">
        <EventList key={selected} changes={effective} onOpen={openChange} />
      </div>}
      {taxItems.length > 0 && <section className="lc-list lc-tax-list" aria-label={`세무일정 ${taxItems.length}건`}>
        <div className="lc-list-head"><h3 className="lc-tax">세무일정 {taxItems.length}건</h3></div>
        <ul>{taxItems.map(item => <li key={item.id}>
          <strong>{item.title}</strong><p>기한 {dateLabel(item.date)}{item.targetPeriod && ` · 대상 ${item.targetPeriod}`}</p>
          <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer">국세청 원문 ↗</a>
        </li>)}</ul>
      </section>}
    </section>
    {!sheetOpen && detailStatus}
    <footer className="lc-footer">
      <h2>공식 원문을 먼저 확인하세요</h2>
      <p>본 서비스는 공개된 법령 자료를 수집·가공하여 제공하며, 수집 지연, 누락, 해석 또는 요약 오류가 발생할 수 있습니다. 법령의 적용 여부는 개별 조항의 시행일, 부칙, 경과조치 및 사실관계에 따라 달라질 수 있습니다. 중요한 법적 판단이나 조치를 하기 전 공식 법령 원문을 직접 확인하고, 필요하면 전문가의 검토를 받으시기 바랍니다. 이 서비스는 개별 사건에 대한 법률 자문을 대신하지 않습니다.</p>
      <p>{LAW_SOURCE}</p>
      <nav aria-label="공식 출처">
        <a href="https://www.law.go.kr/" target="_blank" rel="noopener noreferrer">국가법령정보센터 ↗</a>
        <span>
          <a href="https://github.com/legalize-kr/legalize-kr" target="_blank" rel="noopener noreferrer">legalize-kr ↗</a>
          {' · '}
          <a href="https://github.com/legalize-kr/admrule-kr" target="_blank" rel="noopener noreferrer">admrule-kr ↗</a>
          {' '}(원문: 공공저작물, 메타데이터 MIT)
        </span>
        <a href={LICENSE_PATH}>라이선스</a>
      </nav>
    </footer>
    {sheetOpen && <DateSheet key={selected} date={selected} changes={effective} taxItems={taxItems} demo={snapshot?.demo ?? false}
      calendar={calendar} status={detailStatus} onOpen={openChange} onClose={() => history.back()} />}
    {opened && <ChangeDialog change={opened} demo={snapshot?.demo ?? false}
      onClose={() => layer?.changeId ? history.back() : setOpened(null)} />}
    {holdsOpen && <HoldDialog demo={snapshot?.demo ?? isDemoMode()} onClose={() => setHoldsOpen(false)} />}
  </main>
}
