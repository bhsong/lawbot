import { CalendarPanel } from './CalendarPanel'
import { FeedbackList } from './FeedbackList'
import '@lawbot/ui/calendar.css'
import './admin.css'

export function AdminPage({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [tab, setTab] = useState<'management' | 'cache'>('management')
  return <main className="lc-page admin-page">
    <h1>피드백 &amp; 법령 관리</h1>
    <nav aria-label="관리 메뉴" className="cache-actions">
      <button type="button" aria-pressed={tab === 'management'} onClick={() => setTab('management')}>피드백·법령</button>
      <button type="button" aria-pressed={tab === 'cache'} onClick={() => setTab('cache')}>캐시</button>
    </nav>
    {tab === 'cache' ? <CachePanel onUnauthorized={onUnauthorized} /> : <>
      <CalendarPanel onUnauthorized={onUnauthorized} />
      <div className="admin-grid">
        <FeedbackList label="like" onUnauthorized={onUnauthorized} />
        <FeedbackList label="flag" onUnauthorized={onUnauthorized} />
      </div>
    </>}
  </main>
}
import { useState } from 'react'
import { CachePanel } from './CachePanel'
