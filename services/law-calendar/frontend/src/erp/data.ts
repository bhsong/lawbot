import type { ErpHistory, ErpRules, ErpUpdates } from '../law-calendar/api-types'

export type ErpLoader = (signal: AbortSignal) => Promise<ErpUpdates>
export type ErpRuleLoader = (asOf: string, signal: AbortSignal) => Promise<ErpRules>

export const loadErp: ErpLoader = async (signal) => {
  const response = await fetch('/api/erp-updates', { signal, credentials: 'omit', cache: 'no-store' })
  if (!response.ok) throw new Error('ERP API unavailable')
  return response.json() as Promise<ErpUpdates>
}

export const loadErpRules: ErpRuleLoader = async (asOf, signal) => {
  const response = await fetch('/api/erp-updates/v2?' + new URLSearchParams({ asOf }),
    { signal, credentials: 'omit', cache: 'no-store' })
  if (response.status === 404) return { state: 'unconfigured', asOfDate: asOf }
  if (!response.ok) throw new Error('ERP rule API unavailable')
  return response.json() as Promise<ErpRules>
}

export const ACTION_LABELS: Record<ErpHistory['action'], string> = {
  collected: '새 버전 수집',
  unchanged: '변경 없음 확인',
  attached: '원문 PDF 등록',
  approved: '공개 승인',
  rejected: '반려',
}

/** '2026-11-12T20:05:00+09:00' → '2026-11-12 20:05' (feed timestamps are already KST). */
export function kstMinute(value: string): string {
  return value.slice(0, 16).replace('T', ' ')
}
