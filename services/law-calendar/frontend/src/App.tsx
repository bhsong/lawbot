import { useEffect, useState } from 'react'
import ErpPage from './erp/ErpPage'
import LawCalendarPage from './law-calendar/LawCalendarPage'
import './erp/erp.css'

type Tab = 'calendar' | 'erp'
const TABS: [Tab, string][] = [['calendar', '법령 캘린더'], ['erp', 'ERP 자료']]

function fromHash(): Tab {
  return window.location.hash === '#erp' ? 'erp' : 'calendar'
}

/** Two independent views on one public site; `#erp` links straight to the ERP view. */
export default function App() {
  const [tab, setTab] = useState<Tab>(fromHash)
  useEffect(() => {
    const follow = () => setTab(fromHash())
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])

  function select(next: Tab) {
    window.history.replaceState(null, '', next === 'erp' ? '#erp' : window.location.pathname + window.location.search)
    setTab(next)
  }

  return <>
    <nav className="app-tabs" role="tablist" aria-label="화면 선택">
      {TABS.map(([id, label]) => <button key={id} type="button" role="tab" id={`tab-${id}`}
        aria-selected={tab === id} aria-controls={`panel-${id}`} onClick={() => select(id)}>{label}</button>)}
    </nav>
    <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
      {tab === 'erp' ? <ErpPage /> : <LawCalendarPage />}
    </div>
  </>
}
