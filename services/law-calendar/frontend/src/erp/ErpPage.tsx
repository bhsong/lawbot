import { useEffect, useRef, useState } from 'react'
import { EventList, Modal, todayKst } from '@lawbot/ui'
import type { CalendarChange } from '@lawbot/ui'
import type { ErpItem, ErpRule, ErpRules, ErpUpdates } from '../law-calendar/api-types'
import { ChangeDialog } from '../law-calendar/ChangeDialog'
import { loadCalendar, loadChange } from '../law-calendar/data'
import type { CalendarLoader, CalendarSnapshot } from '../law-calendar/types'
import { ACTION_LABELS, kstMinute, loadErp, loadErpRules } from './data'
import type { ErpLoader, ErpRuleLoader } from './data'
import './erp.css'
import AgencyNews from './AgencyNews'
import RuleCard from './RuleCard'

const KINDS: CalendarChange['kind'][] = ['notice', 'effective', 'bill']
const NOTICES = {
  missing: 'ERP 자료가 아직 준비되지 않았습니다.',
  invalid: 'ERP 자료 파일을 확인하지 못했습니다. 법령 캘린더는 그대로 이용할 수 있습니다.',
}

function shiftMonth(month: string, delta: number): string {
  const [year, value] = month.split('-').map(Number)
  const date = new Date(Date.UTC(year, value - 1 + delta, 1))
  return date.toISOString().slice(0, 7)
}

function ItemCard({ item, reference = false }: { item: ErpItem; reference?: boolean }) {
  const titleId = `erp-item-${item.id}`
  const Container = reference ? 'div' : 'article'
  const Title = reference ? 'h4' : 'h3'
  const dates: [string, string][] = [
    ...(item.effectiveFrom ? [['시행일', item.effectiveFrom] as [string, string]] : []),
    ...(item.asOf ? [['현행 법령본 기준일', item.asOf] as [string, string]] : []),
    ...(item.issuedNo || item.issuedOn ? [['발령', [item.issuedNo && `제${item.issuedNo}호`, item.issuedOn].filter(Boolean).join(' · ')] as [string, string]] : []),
    ...(item.agency ? [['소관', item.agency] as [string, string]] : []),
    ...(item.lawName ? [['근거', [item.lawName, item.article].filter(Boolean).join(' ')] as [string, string]] : []),
  ]
  return <Container className={reference ? 'erp-rule-reference' : 'erp-card'} aria-labelledby={titleId}>
    <p className="erp-tag">{item.sourceKey} · {item.sourceTitle}</p>
    <Title id={titleId}>{item.title}</Title>
    <dl className="erp-dates">{dates.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {item.excerpt && !reference && <p className="erp-excerpt">{item.excerpt}</p>}
    <ul className="erp-links">
      {item.sourceUrl && <li><a href={item.sourceUrl} target="_blank" rel="noopener noreferrer">국가법령정보센터 원문 ↗</a></li>}
      {item.attachments.map((file) => <li key={file.pdfUrl}>
        <a href={file.pdfUrl} target="_blank" rel="noopener noreferrer">{file.title} PDF ↗</a>
        <span className="erp-hash">{file.sha256
          ? `SHA-256 ${file.sha256} · ${file.bytes?.toLocaleString('ko-KR')}바이트`
          : '원본 파일 미등록'}</span>
      </li>)}
    </ul>
  </Container>
}

function RuleSections({ rules, items, available }: { rules: ErpRule[]; items: ErpItem[]; available: boolean }) {
  const keys = new Set(rules.map((rule) => rule.sourceKey))
  const current = rules.filter((rule) => rule.scopes.some((scope) => scope.currentVersion))
  const upcoming = rules.filter((rule) =>
    !current.includes(rule) && rule.scopes.some((scope) => scope.scheduledChanges.length > 0))
  const unverified = rules.filter((rule) => !current.includes(rule) && !upcoming.includes(rule))
  const references = items.filter((item) => !keys.has(item.sourceKey))
  const sections = [
    { id: 'erp-current', title: '현재 적용 중', rules: current, empty: available ? '확인된 현행 기준이 없습니다.' : '' },
    { id: 'erp-upcoming', title: '적용 예정', rules: upcoming, empty: available ? '별도 예정 카드가 없습니다.' : '' },
    { id: 'erp-unverified', title: '현행 기준 확인 필요', rules: unverified, empty: '' },
  ]
  return <>{sections.map((section) => {
    const showReferences = section.id === 'erp-unverified' && references.length > 0
    if (!section.rules.length && !section.empty && !showReferences) return null
    return <section key={section.id} className="erp-section" aria-labelledby={section.id}>
      <h2 id={section.id}>{section.title}</h2>
      {!section.rules.length && section.empty && <p className="lc-empty">{section.empty}</p>}
      {section.rules.map((rule) => {
        const documents = items.filter((item) => item.sourceKey === rule.sourceKey)
        return <RuleCard key={rule.ruleId} rule={rule}>
          {documents.length > 0 && <details className="erp-rule-details">
            <summary>기존 자료와 첨부 {documents.length}건</summary>
            {documents.map((item) => <ItemCard key={item.id} item={item} reference />)}
          </details>}
        </RuleCard>
      })}
      {showReferences && references.map((item) => <ItemCard key={item.id} item={item} reference />)}
    </section>
  })}</>
}

function ItemSection({ id, title, items, empty }: { id: string; title: string; items: ErpItem[]; empty: string }) {
  return <section className="erp-section" aria-labelledby={id}>
    <h2 id={id}>{title}</h2>
    {items.length === 0 ? <p className="lc-empty">{empty}</p> : items.map((item) => <ItemCard key={item.id} item={item} />)}
  </section>
}

function HistoryDialog({ feed, onClose }: { feed: ErpUpdates; onClose: () => void }) {
  const rows = [...(feed.history ?? [])].sort((a, b) => b.at.localeCompare(a.at))
  return <Modal header="수집 이력" closeLabel="수집 이력 닫기" labelledBy="erp-history-title" describedBy="erp-history-note" onClose={onClose}>
    <div className="lc-dialog-body">
    <h2 id="erp-history-title">수집 이력</h2>
    <p id="erp-history-note" className="lc-subtitle">공개된 항목을 언제, 어느 원천에서 가져왔는지 보여 줍니다. 시각은 한국 표준시입니다.</p>
    <div className="erp-table-wrap"><table className="erp-history">
      <thead><tr><th scope="col">시각</th><th scope="col">항목</th><th scope="col">결과</th><th scope="col">원천</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={`${row.itemId}-${row.at}-${index}`}>
        <td>{kstMinute(row.at)}</td><td>{row.sourceKey} {row.title}</td><td>{ACTION_LABELS[row.action]}</td><td>{row.origin ?? '—'}</td>
      </tr>)}</tbody>
    </table></div>
    </div>
  </Modal>
}

export default function ErpPage({ loader = loadErp, ruleLoader = loadErpRules, lawLoader = loadCalendar, today = todayKst() }: {
  loader?: ErpLoader; ruleLoader?: ErpRuleLoader; lawLoader?: CalendarLoader; today?: string
}) {
  const [feed, setFeed] = useState<ErpUpdates | null>(null)
  const [ruleFeed, setRuleFeed] = useState<ErpRules | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [month, setMonth] = useState(today.slice(0, 7))
  const [laws, setLaws] = useState<CalendarSnapshot | null>(null)
  const [lawsFailed, setLawsFailed] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [opened, setOpened] = useState<CalendarChange | null>(null)
  const detailRequest = useRef<AbortController | null>(null)
  useEffect(() => () => detailRequest.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    setState('loading')
    loader(controller.signal).then((data) => {
      if (controller.signal.aborted) return
      setFeed(data)
      setState('ready')
    }, () => { if (!controller.signal.aborted) setState('error') })
    return () => controller.abort()
  }, [loader, attempt])

  useEffect(() => {
    const controller = new AbortController()
    setRuleFeed(null)
    ruleLoader(today, controller.signal).then((data) => {
      if (!controller.signal.aborted) setRuleFeed(data)
    }, () => {
      if (!controller.signal.aborted) setRuleFeed({ state: 'invalid', asOfDate: today })
    })
    return () => controller.abort()
  }, [ruleLoader, today, attempt])

  const related = feed?.relatedLaws ?? []
  const hasRelated = related.length > 0
  useEffect(() => {
    if (!hasRelated) return
    const controller = new AbortController()
    setLaws(null); setLawsFailed(false)
    lawLoader(month, controller.signal).then((data) => {
      if (!controller.signal.aborted) setLaws(data)
    }, () => { if (!controller.signal.aborted) setLawsFailed(true) })
    return () => controller.abort()
  }, [lawLoader, month, hasRelated])

  async function openChange(change: CalendarChange) {
    if (laws?.demo) { setOpened(change); return }
    detailRequest.current?.abort()
    const controller = new AbortController()
    detailRequest.current = controller
    try {
      const detail = await loadChange(change.id, controller.signal)
      if (!controller.signal.aborted) setOpened(detail)
    } catch {
      if (!controller.signal.aborted) setLawsFailed(true)
    }
  }

  const items = feed?.state === 'ok' ? feed.items ?? [] : []
  const current = items.filter((item) => !item.effectiveFrom || item.effectiveFrom <= today)
  const upcoming = items.filter((item) => item.effectiveFrom && item.effectiveFrom > today)
  const showRules = ruleFeed && ruleFeed.state !== 'unconfigured'
  const lawChanges = (laws?.changes ?? []).filter((change) => related.includes(change.lawName))
  const byKind = (kind: CalendarChange['kind']) => lawChanges.filter((change) => change.kind === kind)

  return <main className="lc-page">
    <header className="lc-header">
      <div><p className="lc-eyebrow">LAWBOT / ERP REFERENCE</p><h1>ERP 자료</h1>
        <p className="lc-subtitle">급여·원천징수·4대보험 업무에 쓰는 고시·별표·서식·조문과 적용 기준입니다.</p></div>
      {feed?.state === 'ok' && <button type="button" className="lc-more" onClick={() => setHistoryOpen(true)}>수집 이력</button>}
    </header>
    <aside className="lc-disclaimer" aria-label="ERP 자료 이용 안내">이 페이지는 공식 원문을 검토해 옮긴 참고 자료입니다. 요율·세액·서식을 업무에 적용하기 전 국가법령정보센터 원문과 적용 기간을 다시 확인하세요.</aside>
    <p className="lc-subtitle">
      <a href="https://www.hometax.go.kr/" target="_blank" rel="noopener noreferrer">홈택스 공식 홈</a>
      {' · '}<span>전체메뉴 → 기타 → 고객센터 → 자료실</span>
    </p>
    <AgencyNews />
    {(state === 'loading' || !ruleFeed) && <p className="lc-empty" role="status">ERP 자료를 불러오는 중입니다.</p>}
    {state === 'error' && <div className="lc-warning" role="alert"><p>ERP 자료를 불러오지 못했습니다.</p>
      <button type="button" onClick={() => setAttempt((value) => value + 1)}>다시 시도</button></div>}
    {state === 'ready' && feed && feed.state !== 'ok' && <p className="lc-warning" role="status">{NOTICES[feed.state]}</p>}
    {showRules && <>
      {ruleFeed.state === 'ok'
        ? <p className="lc-data-status"><span>기준일 {ruleFeed.asOfDate} KST</span>
          {ruleFeed.generatedAt && <span>적용 기준 갱신 {kstMinute(ruleFeed.generatedAt)} KST</span>}</p>
        : <div className="lc-warning" role="status"><p>{ruleFeed.state === 'missing'
          ? 'ERP 적용 기준이 아직 준비되지 않았습니다.' : 'ERP 적용 기준을 확인하지 못했습니다.'}</p>
          {state !== 'error' && <button type="button" onClick={() => setAttempt((value) => value + 1)}>다시 시도</button>}</div>}
      <RuleSections rules={ruleFeed.state === 'ok' ? ruleFeed.rules ?? [] : []} items={items}
        available={ruleFeed.state === 'ok'} />
    </>}
    {state === 'ready' && feed?.state === 'ok' && <>
      {ruleFeed?.state === 'unconfigured' && <>
        <p className="lc-data-status"><span>자료 생성: {feed.generatedAt ? kstMinute(feed.generatedAt) : '확인되지 않음'}</span><span>날짜 기준: 한국 표준시(KST)</span></p>
        <ItemSection id="erp-current" title="현재 적용 중" items={current} empty="현재 적용 중인 공개 자료가 없습니다." />
        <ItemSection id="erp-upcoming" title="적용 예정" items={upcoming} empty="적용 예정인 공개 자료가 없습니다." />
      </>}
      {hasRelated && <section className="erp-section" aria-labelledby="erp-related-title">
        <div className="lc-selected"><h2 id="erp-related-title">관련 법령 일정 · {month.replace('-', '년 ')}월</h2>
          <div className="erp-month">
            <button type="button" className="lc-more" onClick={() => setMonth(shiftMonth(month, -1))}>이전 달</button>
            <button type="button" className="lc-more" onClick={() => setMonth(shiftMonth(month, 1))}>다음 달</button>
          </div></div>
        <p className="lc-subtitle">{related.join(', ')}의 입법예고·시행 조문입니다. 항목을 누르면 법령 캘린더의 상세를 엽니다.</p>
        {lawsFailed && <p className="lc-warning" role="alert">법령 일정을 불러오지 못했습니다.</p>}
        {!laws && !lawsFailed && <p className="lc-empty" role="status">법령 일정을 불러오는 중입니다.</p>}
        {laws && lawChanges.length === 0 && <p className="lc-empty">이 달에 관련 법령의 입법예고·시행 조문이 없습니다.</p>}
        {laws && lawChanges.length > 0 && <div className="lc-panels">
          {KINDS.filter((kind) => byKind(kind).length > 0).map((kind) =>
            <EventList key={kind} kind={kind} changes={byKind(kind)} onOpen={openChange} />)}
        </div>}
      </section>}
    </>}
    {historyOpen && feed && <HistoryDialog feed={feed} onClose={() => setHistoryOpen(false)} />}
    {opened && <ChangeDialog change={opened} demo={laws?.demo ?? false} onClose={() => setOpened(null)} />}
  </main>
}
