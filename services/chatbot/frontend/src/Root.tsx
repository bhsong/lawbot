import { lazy, Suspense } from 'react'
import App from './App'

const LawCalendarPage = lazy(() => import('./law-calendar/LawCalendarPage'))

/** Keep the public calendar outside the existing chatbot session lifecycle. */
export function Root() {
  return /^\/law-calendar\/?$/.test(window.location.pathname)
    ? <Suspense fallback={<p role="status">캘린더를 불러오는 중입니다.</p>}><LawCalendarPage /></Suspense>
    : <App />
}
