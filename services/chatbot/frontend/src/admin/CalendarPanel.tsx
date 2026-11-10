import { useCallback, useId, useRef, useState } from 'react'
import { ChangeView, EventList, Modal, MonthCalendar, dateLabel, dateRoleLabel, todayKst } from '@lawbot/ui'
import type { CalendarChange, CalendarDay } from '@lawbot/ui'
import { AdminApiError, adminGet, adminMessage, calendarPath, useAdminRead } from './adminApi'
import { CalendarHoldReview } from './CalendarHoldReview'

type CalendarStatus = { collectedAt: string | null; verifiedAt: string | null; delayed: boolean }
type ChangePage = { items: CalendarChange[]; total: number; page: number }
type HoldKind = 'effective' | 'notice'
type Hold = { id: string; lawName: string; article?: string; billNo?: string; reason: string; sourceUrl: string }
type Page<T> = { items: T[]; total: number; page: number; page_size: number }
type ImportFailure = {
  source: string; event_id: string | null; error_type: string; message: string; occurred_at: string
}

function calendarGet<T>(path: string, signal: AbortSignal) {
  return adminGet<T>(`/calendar/${path}`, signal)
}

async function loadCalendar(month: string, signal: AbortSignal) {
  const [calendar, status] = await Promise.all([
    calendarGet<{ days: CalendarDay[] }>(`law-calendar?month=${month}`, signal),
    calendarGet<CalendarStatus>('law-calendar/status', signal),
  ])
  const changes: CalendarChange[] = []
  for (let page = 1; page <= 100; page++) {
    const data = await calendarGet<ChangePage>(`law-events?month=${month}&page=${page}&page_size=100`, signal)
    if (!Array.isArray(data.items) || data.page !== page || data.total < changes.length + data.items.length) break
    changes.push(...data.items)
    if (changes.length === data.total) return { changes, counts: calendar.days, status }
    if (!data.items.length) break
  }
  throw new AdminApiError(502, '', '캘린더 일정 전체를 불러오지 못했습니다.')
}

function readError(error: unknown, retry: () => void, onClose?: () => void) {
  return <p role="alert" className="admin-error">{adminMessage(error)}{' '}
    <button type="button" onClick={retry}>다시 시도</button>{onClose && <> <button type="button" onClick={onClose}>닫기</button></>}
  </p>
}

function CalendarDetail({ changeId, onClose, onUnauthorized }: {
  changeId: string; onClose: () => void; onUnauthorized: () => void
}) {
  const id = useId()
  const load = useCallback((signal: AbortSignal) =>
    calendarGet<CalendarChange>(`law-changes/${encodeURIComponent(changeId)}`, signal), [changeId])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)
  if (error != null) return readError(error, retry, onClose)
  if (!data) return <p role="status">법령 변경 상세를 불러오는 중입니다.</p>
  const header = data.assembly ? dateRoleLabel(data.assembly.dateRole)
    : data.kind === 'notice' ? '입법예고 · 게시일 기준' : '시행 조문 · 시행일 기준'
  return <Modal header={header} closeLabel="변경 상세 닫기" labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onClose}>
    <ChangeView change={data} demo={false} titleId={`${id}-title`} descriptionId={`${id}-description`}
      comparisonPageSrc={(change, page) => calendarPath(`law-changes/${encodeURIComponent(change)}/notice-comparison/${page}`)} />
  </Modal>
}

function holdTitle(hold: Hold) {
  return [hold.lawName, hold.billNo ? `의안번호 ${hold.billNo}` : hold.article].filter(Boolean).join(' ')
}

function Holds({ onClose, onUnauthorized, onReviewed }: {
  onClose: () => void; onUnauthorized: () => void; onReviewed: (date?: string) => void
}) {
  const id = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const [kind, setKind] = useState<HoldKind>('effective')
  const [page, setPage] = useState(1)
  const [hold, setHold] = useState<Hold | null>(null)
  const [saved, setSaved] = useState(false)
  const load = useCallback((signal: AbortSignal) =>
    calendarGet<Page<Hold>>(`law-holds?type=${kind}&page=${page}&page_size=20`, signal), [kind, page])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)

  function select(next: HoldKind) {
    setKind(next)
    setPage(1)
  }

  function back() {
    setHold(null)
    heading.current?.focus()
  }

  function reviewed(date?: string) {
    back()
    setSaved(true)
    if (page > 1 && data?.items.length === 1) setPage(page - 1)
    else retry()
    onReviewed(date)
  }

  return <Modal header="법령 캘린더" closeLabel="보류 목록 닫기" labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onClose}>
    <div className="lc-dialog-body admin-holds">
      <h2 ref={heading} tabIndex={-1} id={`${id}-title`}>보류 목록</h2>
      <p id={`${id}-description`}>시행일 또는 예고기간 확인이 필요한 항목입니다.</p>
      {hold ? <CalendarHoldReview kind={kind} holdId={hold.id} title={holdTitle(hold)} reason={hold.reason}
        onBack={back} onReviewed={reviewed} onUnauthorized={onUnauthorized} /> : <>
        {saved && <p role="status">검토 결정을 저장했습니다.</p>}
        <div className="admin-actions" role="group" aria-label="보류 유형">
          <button type="button" aria-pressed={kind === 'effective'} onClick={() => select('effective')}>시행일 보류</button>
          <button type="button" aria-pressed={kind === 'notice'} onClick={() => select('notice')}>입법예고 검토 대기</button>
        </div>
        {error != null ? readError(error, retry) : !data ? <p role="status">보류 목록을 불러오는 중입니다.</p> : <>
          <p aria-live="polite">총 {data.total}건 · {data.page}페이지</p>
          {data.items.length ? <ul className="admin-calendar-list">{data.items.map((item) => <li key={item.id}>
            <strong>{holdTitle(item)}</strong>
            <p>{item.reason}</p>
            <div className="admin-actions">
              <button type="button" aria-label={`검토 열기 · ${holdTitle(item)}`}
                onClick={() => { setSaved(false); setHold(item) }}>검토 열기</button>
              {item.sourceUrl?.startsWith('https://') && <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer">공식 원문 확인 (새 창)</a>}
            </div>
          </li>)}</ul> : <p>현재 공개된 보류 항목이 없습니다.</p>}
          <div className="admin-actions">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>이전 보류 목록</button>
            <button type="button" disabled={page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>다음 보류 목록</button>
          </div>
        </>}
      </>}
    </div>
  </Modal>
}

function ImportFailures({ onClose, onUnauthorized }: { onClose: () => void; onUnauthorized: () => void }) {
  const id = useId()
  const [page, setPage] = useState(1)
  const load = useCallback((signal: AbortSignal) =>
    calendarGet<Page<ImportFailure>>(`import-failures?page=${page}&page_size=20`, signal), [page])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)

  return <Modal header="법령 캘린더" closeLabel="가져오기 실패 닫기" labelledBy={`${id}-title`} describedBy={`${id}-description`} onClose={onClose}>
    <div className="lc-dialog-body admin-holds">
      <h2 id={`${id}-title`}>가져오기 실패</h2>
      <p id={`${id}-description`}>가져오기 중 발생한 오류 기록입니다.</p>
      {error != null ? readError(error, retry) : !data ? <p role="status">실패 기록을 불러오는 중입니다.</p> : <>
        <p aria-live="polite">총 {data.total}건 · {data.page}페이지</p>
        {data.items.length ? <ul className="admin-calendar-list">{data.items.map((item, index) =>
          <li key={`${item.source}-${item.event_id}-${item.occurred_at}-${index}`}>
            <strong>{item.source} · {item.error_type}</strong>
            <p>{item.message}</p>
            <p className="admin-meta">이벤트 <code>{item.event_id ?? '확인되지 않음'}</code> · <time dateTime={item.occurred_at}>{item.occurred_at}</time></p>
          </li>)}</ul> : <p>가져오기 실패 기록이 없습니다.</p>}
        <div className="admin-actions">
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>이전 실패 목록</button>
          <button type="button" disabled={page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>다음 실패 목록</button>
        </div>
      </>}
    </div>
  </Modal>
}

export function CalendarPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const id = useId()
  const [date, setDate] = useState(todayKst)
  const [changeId, setChangeId] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'holds' | 'failures' | null>(null)
  const month = date.slice(0, 7)
  const load = useCallback((signal: AbortSignal) => loadCalendar(month, signal), [month])
  const { data, error, retry } = useAdminRead(load, onUnauthorized)
  const missing = error instanceof AdminApiError && error.status === 503 && error.code === 'calendar_not_configured'
  const changes = data?.changes ?? []
  const selected = changes.filter((change) => change.date === date)

  function select(next: string) {
    setChangeId(null)
    setDate(next)
  }

  function reviewed(next?: string) {
    if (next) select(next)
    retry()
  }

  function openDialog(next: 'holds' | 'failures') {
    setChangeId(null)
    setDialog(next)
  }

  return <section aria-labelledby={id} className="admin-card admin-calendar">
    <h2 id={id}>법령 캘린더</h2>
    {!missing && <div className="admin-actions admin-calendar-actions">
      <button type="button" onClick={() => openDialog('holds')}>보류 목록</button>
      <button type="button" onClick={() => openDialog('failures')}>가져오기 실패</button>
    </div>}
    {missing ? <p>캘린더 연결 안 됨</p> : error != null ? readError(error, retry) : <>
      {!data && <p role="status">캘린더를 불러오는 중입니다.</p>}
      {data && <>
        <p>수집 시각 {data.status.collectedAt ?? '확인되지 않음'} · 검증 시각 {data.status.verifiedAt ?? '확인되지 않음'} · KST</p>
        {data.status.delayed && <p role="status" className="lc-warning">데이터 갱신이 지연되고 있습니다.</p>}
      </>}
      <MonthCalendar selected={date} changes={changes} totals={data?.counts} onSelect={select} />
      {data && <>
        <h3>{dateLabel(date)}</h3>
        <div className="admin-grid">
          {(['notice', 'effective', 'bill'] as const).map((kind) => {
            const events = selected.filter((change) => change.kind === kind)
            return (kind !== 'bill' || events.length > 0) && <EventList key={`${date}-${kind}`} kind={kind} changes={events}
              onOpen={(change) => setChangeId(change.id)} />
          })}
        </div>
      </>}
      {changeId && <CalendarDetail key={changeId} changeId={changeId} onClose={() => setChangeId(null)} onUnauthorized={onUnauthorized} />}
    </>}
    {dialog === 'holds' && <Holds onClose={() => setDialog(null)} onUnauthorized={onUnauthorized} onReviewed={reviewed} />}
    {dialog === 'failures' && <ImportFailures onClose={() => setDialog(null)} onUnauthorized={onUnauthorized} />}
  </section>
}
