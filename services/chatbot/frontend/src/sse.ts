/** Incremental parser for the server-sent events of POST /api/chat (one JSON object per `data:` line). */
export function sseParser(onEvent: (event: string, data: unknown) => void) {
  let buf = ''

  function block(text: string) {
    let event: string | undefined
    let data: string | undefined
    for (const line of text.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data = line.slice(6)
    }
    if (event && data !== undefined) onEvent(event, JSON.parse(data))
  }

  return {
    feed(chunk: string) {
      buf = (buf + chunk).replace(/\r\n/g, '\n')
      let end: number
      while ((end = buf.indexOf('\n\n')) >= 0) {
        block(buf.slice(0, end))
        buf = buf.slice(end + 2)
      }
    },
    end() {
      if (buf.trim()) block(buf)
      buf = ''
    },
  }
}
