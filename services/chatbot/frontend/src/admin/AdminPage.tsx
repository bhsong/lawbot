import { FeedbackList } from './FeedbackList'
import '@lawbot/ui/calendar.css'
import './admin.css'

export function AdminPage({ onUnauthorized }: { onUnauthorized: () => void }) {
  return <main className="lc-page admin-page">
    <h1>피드백 &amp; 법령 관리</h1>
    <div className="admin-grid">
      <FeedbackList label="like" onUnauthorized={onUnauthorized} />
      <FeedbackList label="flag" onUnauthorized={onUnauthorized} />
    </div>
  </main>
}
