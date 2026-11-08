import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode, RefObject } from 'react'

export function Modal({ header, closeLabel, labelledBy, describedBy, initialFocus, onClose, children }: {
  header: string; closeLabel: string; labelledBy: string; describedBy: string; onClose: () => void; children: ReactNode
  initialFocus?: RefObject<HTMLElement | null>
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    initialFocus?.current?.focus()
    return () => { element.close(); previous?.focus() }
  }, [initialFocus])

  function keepFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const elements = [...dialog.current!.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], summary, [tabindex="0"]')]
      .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length > 0)
    const first = elements[0], last = elements[elements.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
  return <dialog ref={dialog} className="lc-dialog" aria-labelledby={labelledBy} aria-describedby={describedBy}
    onKeyDown={keepFocus} onCancel={(event) => { event.preventDefault(); onClose() }}>
    <div className="lc-dialog-header">
      <span>{header}</span>
      <button type="button" autoFocus={!initialFocus} aria-label={closeLabel} onClick={onClose}>닫기 ×</button>
    </div>
    {children}
  </dialog>
}
