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
type SheetView = { limit: number; scrollTop: number; lastChangeId?: string }
type CalendarLayer = { date: string; sheet?: SheetView; changeId?: string }
type HistoryLayer = CalendarLayer & { position: number }
type DetailState = CalendarChange | 'loading' | 'error' | null
type MonthResult = { month: string; snapshot: CalendarSnapshot | null }

function currentLayer(): HistoryLayer | null {
  return history.state?.lawCalendar ?? null
}

function depth(layer: CalendarLayer | null) {
  return Number(!!layer?.sheet) + Number(!!layer?.changeId)
}

function navigationIndex() {
  const index = window.navigation?.currentEntry?.index
  return index === -1 ? undefined : index
}

export default function LawCalendarPage({ loader = loadCalendarWithTax, initialDate }: { loader?: CalendarLoader; initialDate?: string }) {
  const [chosen, setChosen] = useState(initialDate ?? todayKst)
  const [result, setResult] = useState<MonthResult | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [scenario, setScenario] = useState<Scenario>('normal')
  const [detail, setDetail] = useState<DetailState>(null)
  const [holdsOpen, setHoldsOpen] = useState(false)
  const [layer, setLayer] = useState(currentLayer)
  const cursor = useRef({ index: navigationIndex(), depth: depth(layer) })
  const direction = useRef<-1 | 1>(-1)
  const calendar = useRef<HTMLDivElement>(null)
  const dateClick = useRef<HTMLButtonElement | null>(null)
  const requested = layer?.date ?? chosen
  const detailRequest = useRef<AbortController | null>(null)
  useEffect(() => () => detailRequest.current?.abort(), [])
  const month = requested.slice(0, 7)
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
  const taxData = snapshot?.tax
  const taxCounts = new Map<string, number>()
  for (const item of taxData?.items ?? []) taxCounts.set(item.date, (taxCounts.get(item.date) ?? 0) + 1)
  const layerChange = layer?.changeId
    ? changes.find(item => item.date === layer.date && item.id === layer.changeId) : undefined
  const active = layer && state === 'ready' && window.matchMedia?.(MOBILE).matches
    && (layer.changeId ? !!layerChange : layer.sheet && (taxCounts.has(layer.date) || changes.some(item => item.date === layer.date)))
    ? layer : null
  const selected = active?.date ?? chosen
  const effective = changes.filter(change => change.date === selected)
  const taxItems = taxData?.items.filter(item => item.date === selected) ?? []
  const sheet = active?.sheet
  const detailStatus = detail === 'loading' ? <p role="status">상세 정보를 불러오는 중입니다.</p>
    : detail === 'error' && !layer?.changeId ? <p className="lc-warning" role="alert">상세 정보를 확인하지 못했습니다. 항목을 다시 선택하세요.</p> : null

  useEffect(() => {
    function onPop() {
      if (location.hash === '#erp') return
      const next = currentLayer()
      const previous = cursor.current
      const index = navigationIndex()
      const nextDepth = depth(next)
      // Without the Navigation API, the app's own entries are ordered by layer depth.
      const forward = index !== undefined && previous.index !== undefined
        ? index > previous.index : nextDepth > previous.depth
      direction.current = forward ? 1 : -1
      cursor.current = { index, depth: nextDepth }
      detailRequest.current?.abort()
      setDetail(value => value === 'error' && nextDepth < previous.depth ? value : null)
      setLayer(next)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const loadRecordedDetail = useEffectEvent((change: CalendarChange) => loadDetail(change, true))
  useEffect(() => {
    if (!layer || state === 'loading') return
    if (!active) {
      // At the forward edge, return to a usable entry rather than leave a stale entry active.
      const hasForward = window.navigation?.canGoForward ?? layer.position < history.length - 1
      history.go(direction.current === 1 && hasForward ? 1 : -1)
      return
    }
    setChosen(active.date)
    if (layerChange) loadRecordedDetail(layerChange)
  }, [layer, state, active, layerChange])

  function replaceLayer(next: HistoryLayer) {
    history.replaceState({ ...history.state, lawCalendar: next }, '')
    setLayer(next)
  }

  function pushLayer(next: CalendarLayer) {
    history.pushState({ ...history.state, lawCalendar: next }, '')
    // A push discards forward entries, so its position is now known even without Navigation.
    replaceLayer({ ...next, position: history.length - 1 })
    cursor.current = { index: navigationIndex(), depth: depth(next) }
  }

  function resetDetail() {
    detailRequest.current?.abort()
    setDetail(null)
  }

  function select(day: string) {
    const clicked = dateClick.current
    dateClick.current = null
    resetDetail()
    setChosen(day)
    if (layer) { history.go(-depth(layer)); return }
    if (clicked && state === 'ready' && window.matchMedia?.(MOBILE).matches
      && (changes.some(change => change.date === day) || taxCounts.has(day))) {
      pushLayer({ date: day, sheet: { limit: 8, scrollTop: 0 } })
    }
  }

  function openChange(change: CalendarChange, scrollTop = 0) {
    if (layer?.changeId) return
    if (layer?.sheet) {
      const view = { ...layer.sheet, lastChangeId: change.id, scrollTop }
      replaceLayer({ ...layer, sheet: view })
      pushLayer({ date: layer.date, sheet: view, changeId: change.id })
    } else if (window.matchMedia?.(MOBILE).matches) {
      pushLayer({ date: change.date, changeId: change.id })
    } else {
      loadDetail(change, false)
    }
  }

  function loadDetail(change: CalendarChange, recorded: boolean) {
    detailRequest.current?.abort()
    if (snapshot?.demo) { setDetail(change); return }
    const controller = new AbortController()
    detailRequest.current = controller
    setDetail('loading')
    loadChange(change.id, controller.signal).then(result => {
      if (!controller.signal.aborted) setDetail(result)
    }, () => {
      if (controller.signal.aborted) return
      setDetail('error')
      if (recorded) history.back()
    })
  }
  function retry() { setScenario('normal'); setAttempt((value) => value + 1) }

  return <main className="lc-page">
    <header className="lc-header">
      <div><p className="lc-eyebrow">LAWBOT / LAW CALENDAR</p><h1>법령 변경 캘린더</h1>
        <p className="lc-subtitle">시행되는 조문을 날짜별로 확인하세요.</p></div>
      <button type="button" className="lc-more" onClick={() => { resetDetail(); if (layer?.changeId) history.back(); setHoldsOpen(true) }}>보류 목록</button>
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
    {!sheet && detailStatus}
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
    {sheet && active && <DateSheet key={selected} date={selected} changes={effective} taxItems={taxItems} demo={snapshot?.demo ?? false}
      calendar={calendar} status={detailStatus} onOpen={openChange} onClose={() => history.back()}
      limit={sheet.limit} scrollTop={sheet.scrollTop} lastChangeId={sheet.lastChangeId} detailStep={!!active.changeId}
      onLimitChange={limit => replaceLayer({ ...active, sheet: { ...sheet, limit } })} />}
    {detail && typeof detail !== 'string' && <ChangeDialog change={detail} demo={snapshot?.demo ?? false}
      onClose={() => layer?.changeId ? history.back() : resetDetail()} />}
    {holdsOpen && <HoldDialog demo={snapshot?.demo ?? isDemoMode()} onClose={() => setHoldsOpen(false)} />}
  </main>
}
