/** Shapes of the lawbot-web API (services/chatbot/src/lawbot/web.py). */
export type SourceItem = { title: string; scores: string; body: string; decree: boolean }
export type SourceGroup = { label: string | null; open: boolean; items: SourceItem[] }
export type Sources = { groups: SourceGroup[] }

export type ModelOption = { key: string; label: string; info: string; format: string }
export type Option = { value: string; label: string }

export type Config = {
  title: string
  intro: string
  notice: string
  models: ModelOption[]
  disabled: { key: string; label: string }[]
  default_model: string
  formats: Option[]
  examples: string[]
  sources_title: string
  feedback: { enabled: boolean; reasons: Option[] }
}

export type Feedback = { label: 'like' | 'flag'; reason?: string } | null

type CalculationRow = { label: string; value: number | string; unit?: string }
export type Calculation = {
  calculator: string | null
  status: 'calculated' | 'guidance' | 'none'
  year?: number
  inputs?: (CalculationRow & { source: string })[]
  steps?: (CalculationRow & { formula: string; article: string })[]
  results?: (CalculationRow & { condition?: string })[]
  articles?: string[]
  message?: string
}

export type CalculationCheck = 'match' | 'mismatch' | 'missing'

export type Turn = {
  id: string
  question: string
  model: string
  format: string
  status?: string
  text: string
  sources?: Sources
  calculation?: Calculation
  calculationCheck?: CalculationCheck
  stats?: string
  done: boolean
  stopped?: boolean
  error?: string
  turnId?: string | null
  feedback?: Feedback
}
