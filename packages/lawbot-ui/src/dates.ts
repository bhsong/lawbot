/** Calendar dates are KST civil dates; UTC arithmetic avoids browser timezone shifts. */
export function todayKst(now = new Date()): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(now)
}

export function shiftDay(day: string, amount: number): string {
  const value = new Date(`${day}T00:00:00Z`)
  value.setUTCDate(value.getUTCDate() + amount)
  return value.toISOString().slice(0, 10)
}

export function shiftMonth(day: string, amount: number): string {
  const value = new Date(`${day}T00:00:00Z`)
  const date = value.getUTCDate()
  value.setUTCDate(1)
  value.setUTCMonth(value.getUTCMonth() + amount)
  const last = new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + 1, 0)).getUTCDate()
  value.setUTCDate(Math.min(date, last))
  return value.toISOString().slice(0, 10)
}

export function monthDays(month: string): string[] {
  const first = `${month}-01`
  const next = shiftMonth(first, 1)
  const last = shiftDay(next, -1)
  const start = shiftDay(first, -weekday(first))
  const end = shiftDay(last, 6 - weekday(last))
  const days: string[] = []
  for (let day = start; day <= end; day = shiftDay(day, 1)) days.push(day)
  return days
}

export function weekday(day: string): number {
  return new Date(`${day}T00:00:00Z`).getUTCDay()
}

export function dateLabel(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return `${year}년 ${month}월 ${date}일`
}
