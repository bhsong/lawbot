import { useEffect, useRef, useState } from 'react'
import type { ErpNews, PublicNewsBoard } from '../law-calendar/api-types'
import { kstMinute } from './data'

const AGENCIES = {
  lawgo: '국가법령정보센터',
  moel: '고용노동부',
  mofe: '재정경제부',
  nts: '국세청',
  hometax: '홈택스',
} satisfies Record<ErpNews['agency'], string>
type Agency = keyof typeof AGENCIES

const STATUS: Record<PublicNewsBoard['status'], string> = {
  ok: '정상', disabled: '비활성', unverified: '경로 확인 필요', pending: '수집 대기',
  delayed: '수집 지연', blocked: '접근 제한', error: '수집 실패', partial: '일부 수집 실패',
}

const HOMETAX: ErpNews = {
  agency: 'hometax', state: 'link_only', officialHome: 'https://www.hometax.go.kr/',
  navigation: '전체메뉴 → 기타 → 고객센터 → 자료실',
}

function NewsContent({ agency }: { agency: Agency }) {
  const [result, setResult] = useState<ErpNews | 'error' | null>(agency === 'hometax' ? HOMETAX : null)

  useEffect(() => {
    if (agency === 'hometax') return
    const controller = new AbortController()
    fetch('/api/erp-news/latest?agency=' + agency, { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
      .then(async (response) => {
        if (!response.ok) throw new Error('News unavailable')
        const data: ErpNews = await response.json()
        if (!controller.signal.aborted) setResult(data)
      })
      .catch(() => { if (!controller.signal.aborted) setResult('error') })
    return () => controller.abort()
  }, [agency])

  if (result === null) return <p role="status">소식을 불러오는 중입니다.</p>
  if (result === 'error' || result.state === 'invalid' || result.state === 'unavailable')
    return <p role="status">기관 소식을 불러오지 못했습니다.</p>
  if (result.state === 'missing') return <p role="status">기관 소식 스냅샷이 아직 준비되지 않았습니다.</p>

  const items = result.items ?? []
  return <>
    {result.state === 'ok' && <p className="lc-caption">공개 소식 최대 10건 · 등록된 수집 범위 기준</p>}
    {result.generatedAt && <p className="lc-caption">스냅샷 {kstMinute(result.generatedAt)} KST</p>}
    {result.state === 'ok' && items.length === 0 && <p role="status">공개된 소식이 없습니다.</p>}
    {items.length > 0 && <ul className="erp-news-items">
      {items.map((item) => <li key={item.id}>
        <strong>{item.title}</strong>
        <div className="lc-caption">
          {item.board} · {agency === 'lawgo' ? '공포일' : '게시일'} {item.publishedOn ?? '미확인'}
          {item.effectiveOn && ' · 시행일 ' + item.effectiveOn}
        </div>
        <a href={item.officialUrl} target="_blank" rel="noopener noreferrer" aria-label={item.title + ' 원문 열기'}>
          원문 열기 ↗
        </a>
      </li>)}
    </ul>}
    {(result.boards?.length ?? 0) > 0 && <details className="erp-news-status" open>
      <summary>게시판별 수집 상태</summary>
      <ul>{result.boards?.map((board, index) => <li key={index}>
        {board.name} · {STATUS[board.status]}
        <small>마지막 성공 {board.lastSuccessAt ? kstMinute(board.lastSuccessAt) + ' KST' : '기록 없음'}</small>
      </li>)}</ul>
    </details>}
    <p><a href={result.officialHome} target="_blank" rel="noopener noreferrer">{AGENCIES[agency]} 누리집 열기 ↗</a></p>
    {result.navigation && <p>{result.navigation}</p>}
  </>
}

export default function AgencyNews() {
  const [active, setActive] = useState<Agency | null>(null)
  const region = useRef<HTMLElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)
  const restoring = useRef(false)
  const focusPanel = useRef(false)

  useEffect(() => {
    if (active && focusPanel.current) {
      focusPanel.current = false
      closeButton.current?.focus()
    }
  }, [active])

  function close(restore = false) {
    setActive(null)
    focusPanel.current = false
    if (restore) {
      restoring.current = true
      trigger.current?.focus()
      restoring.current = false
    }
  }

  useEffect(() => {
    if (!active) return
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !document.querySelector('dialog[open]')) {
        event.preventDefault()
        close(region.current?.contains(document.activeElement) ?? false)
      }
    }
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !region.current?.contains(event.target)) close()
    }
    document.addEventListener('keydown', keydown)
    document.addEventListener('pointerdown', outside)
    return () => {
      document.removeEventListener('keydown', keydown)
      document.removeEventListener('pointerdown', outside)
    }
  }, [active])

  function open(agency: Agency, button: HTMLButtonElement, focus = false) {
    if (restoring.current) return
    trigger.current = button
    if (focus && active === agency) closeButton.current?.focus()
    else focusPanel.current = focus
    setActive(agency)
  }

  return <aside ref={region} className="erp-news" aria-label="기관별 최신 소식"
    onPointerLeave={(event) => {
      if (event.pointerType === 'mouse' && !region.current?.contains(document.activeElement)) close()
    }}
    onBlur={(event) => {
      if (event.relatedTarget instanceof Node && !region.current?.contains(event.relatedTarget)) close()
    }}>
    <div className="erp-news-tabs">
      {(Object.entries(AGENCIES) as [Agency, string][]).map(([agency, label]) => <button key={agency}
        type="button" aria-expanded={active === agency} aria-controls={active === agency ? 'erp-news-panel' : undefined}
        onPointerEnter={(event) => { if (event.pointerType === 'mouse') open(agency, event.currentTarget) }}
        onFocus={(event) => { if (event.currentTarget.matches(':focus-visible')) open(agency, event.currentTarget) }}
        onClick={(event) => open(agency, event.currentTarget, true)}>
        {label}
      </button>)}
    </div>
    {active && <section className="erp-news-panel" id="erp-news-panel" aria-labelledby="erp-news-title" role="dialog">
      <header>
        <h2 id="erp-news-title">{AGENCIES[active]} 최신 소식</h2>
        <button ref={closeButton} type="button" className="lc-more" onClick={() => close(true)}>닫기</button>
      </header>
      <NewsContent key={active} agency={active} />
    </section>}
  </aside>
}
