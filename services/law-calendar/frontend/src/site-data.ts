import type { CalendarIndex } from './law-calendar/api-types'

export class DataUnavailable extends Error {
  constructor(readonly status: number) {
    super('Calendar data unavailable')
  }
}

export async function readData<T>(signal: AbortSignal, ...segments: string[]): Promise<T> {
  const path = '/data/' + segments.map(encodeURIComponent).join('/') + '.json'
  const response = await fetch(path, { signal, credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) throw new DataUnavailable(response.status)
  return response.json() as Promise<T>
}

export function readIndex(signal: AbortSignal): Promise<CalendarIndex> {
  return readData(signal, 'law-calendar', 'months')
}

export function isDemoMode() {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get('source') !== 'data'
}
