import type { CalendarLoader } from './types'

/** A production build never imports fixtures or fetches unapproved monitoring output. */
export const loadCalendar: CalendarLoader = async (month, signal) => {
  if (import.meta.env.DEV) {
    const { loadDemo } = await import('./demo')
    return loadDemo(month, signal)
  }
  return { changes: [], demo: false, delayed: false, collectedAt: null, verifiedAt: null }
}
