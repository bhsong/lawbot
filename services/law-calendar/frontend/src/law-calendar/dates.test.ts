import { monthDays, shiftDay, shiftMonth, todayKst } from './dates'

test('today uses the Korean civil date across the UTC day boundary', () => {
  expect(todayKst(new Date('2026-11-04T15:01:00Z'))).toBe('2026-11-05')
  expect(todayKst(new Date('2026-11-04T14:59:00Z'))).toBe('2026-11-04')
})

test('day navigation crosses years and leap days', () => {
  expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01')
  expect(shiftDay('2024-03-01', -1)).toBe('2024-02-29')
  expect(shiftDay('2026-03-01', -1)).toBe('2026-02-28')
})

test('month navigation clamps the date rather than skipping a month', () => {
  expect(shiftMonth('2026-01-31', 1)).toBe('2026-02-28')
  expect(shiftMonth('2024-03-31', -1)).toBe('2024-02-29')
  expect(shiftMonth('2026-12-30', 1)).toBe('2027-01-30')
})

test('month grid has whole weeks, including adjacent months and leap day', () => {
  const days = monthDays('2024-02')
  expect(days[0]).toBe('2024-01-28')
  expect(days.at(-1)).toBe('2024-03-02')
  expect(days).toContain('2024-02-29')
  expect(days.length % 7).toBe(0)
  expect(monthDays('2026-05')).toHaveLength(42)
})
