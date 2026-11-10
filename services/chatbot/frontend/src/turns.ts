import type { Calculation, CalculationCheck, Sources, Turn } from './types'

let seq = 0

export function newTurn(question: string, model: string, format: string): Turn {
  seq += 1
  return { id: `t${seq}`, question, model, format, text: '', done: false }
}

type Data = Record<string, unknown>

/** Apply one chat event to a turn (pure). Unknown events leave the turn unchanged. */
export function applyEvent(turn: Turn, event: string, data: unknown): Turn {
  const d = data as Data
  switch (event) {
    case 'status':
      return { ...turn, status: d.text as string }
    case 'sources':
      return { ...turn, sources: data as Sources }
    case 'calculation':
      return { ...turn, calculation: data as Calculation }
    case 'calculation_check':
      return { ...turn, calculationCheck: d.status as CalculationCheck }
    case 'answer':
      return { ...turn, text: d.text as string, status: undefined }
    case 'stats':
      return { ...turn, stats: d.text as string }
    case 'done':
      return { ...turn, text: d.text as string, status: undefined, done: true, turnId: (d.turn_id as string) ?? null }
    case 'error':
      return { ...turn, status: undefined, done: true, error: d.message as string }
    default:
      return turn
  }
}
