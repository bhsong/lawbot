import type { CalendarIndex } from './law-calendar/api-types'

export class DataUnavailable extends Error {
  constructor(readonly missing: boolean) {
    super('Calendar data unavailable')
  }
}

export async function readData<T>(signal: AbortSignal, ...segments: string[]): Promise<T> {
  const path = '/data/' + segments.map(encodeURIComponent).join('/') + '.json'
  const response = await fetch(path, { signal, credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) throw new DataUnavailable(response.status === 404)
  if (response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase() !== 'application/json') {
    throw new DataUnavailable(true)
  }
  try {
    return await response.json() as T
  } catch (error) {
    if (error instanceof SyntaxError) throw new DataUnavailable(true)
    throw error
  }
}

export function readIndex(signal: AbortSignal): Promise<CalendarIndex> {
  return readData(signal, 'law-calendar', 'months')
}

export function isDemoMode() {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get('source') !== 'data'
}
