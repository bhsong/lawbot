import { render } from '@testing-library/react'
import { highlightedText } from './ChangeDialog'

test('highlighting preserves complete source text, newlines and Unicode', () => {
  const before = '제1조\n① 신청은 서면으로 한다. 📝\n② 기록한다.'
  const after = '제1조\n① 신청은 서면 또는 전자문서로 한다. 📝\n② 기록한다.'
  const { container } = render(<div>{highlightedText(after, before, 'ins')}</div>)
  expect(container.textContent).toBe(after)
  expect(container.querySelector('ins')).toHaveTextContent('또는 전자문서')
})

test('literal markup in source text cannot inject HTML', () => {
  const text = '<script>alert(1)</script>\n신설'
  const { container } = render(<div>{highlightedText(text, '', 'ins')}</div>)
  expect(container.textContent).toBe(text)
  expect(container.querySelector('script')).toBeNull()
})
