import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import LawCalendarPage from './law-calendar/LawCalendarPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LawCalendarPage />
  </StrictMode>,
)
