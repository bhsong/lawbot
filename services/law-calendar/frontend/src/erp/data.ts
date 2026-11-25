import type { ErpHistory, ErpRules, ErpUpdates } from '../law-calendar/api-types'
import { DataUnavailable, readData, readIndex } from '../site-data'

export type ErpLoader = (signal: AbortSignal) => Promise<ErpUpdates>
export type ErpRuleLoader = (asOf: string, signal: AbortSignal) => Promise<ErpRules>

export const loadErp: ErpLoader = (signal) => readData(signal, 'erp-updates')

export const loadErpRules: ErpRuleLoader = async (asOf, signal) => {
  const { erpRuleDates } = await readIndex(signal)
  for (const date of erpRuleDates.filter(date => date <= asOf).sort().reverse()) {
    try {
      return await readData<ErpRules>(signal, 'erp-rules', date)
    } catch (error) {
      if (!(error instanceof DataUnavailable) || !error.missing) throw error
    }
  }
  return { state: 'missing', asOfDate: asOf }
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
