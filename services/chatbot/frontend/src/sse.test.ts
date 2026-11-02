import { sseParser } from './sse'

function collect() {
  const got: [string, unknown][] = []
  const p = sseParser((event, data) => got.push([event, data]))
  return { got, p }
}

test('parses events split across chunks', () => {
  const { got, p } = collect()
  p.feed('event: answer\nda')
  p.feed('ta: {"text": "한글\\n줄"}\n')
  expect(got).toEqual([])
  p.feed('\nevent: done\ndata: {"text": "끝"}\n\n')
  expect(got).toEqual([
    ['answer', { text: '한글\n줄' }],
    ['done', { text: '끝' }],
  ])
})

test('accepts CRLF and ignores blocks without event or data', () => {
  const { got, p } = collect()
  p.feed(': comment\r\n\r\nevent: status\r\ndata: {"text": "a"}\r\n\r\ndata: {"x": 1}\n\n')
  expect(got).toEqual([['status', { text: 'a' }]])
})

test('flushes the last block on end', () => {
  const { got, p } = collect()
  p.feed('event: done\ndata: {"text": "x"}')
  p.end()
  expect(got).toEqual([['done', { text: 'x' }]])
})
