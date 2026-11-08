import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'

export function Modal({ header, closeLabel, labelledBy, describedBy, onClose, children }: {
  header: string; closeLabel: string; labelledBy: string; describedBy: string; onClose: () => void; children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    return () => { element.close(); previous?.focus() }
  }, [])

  function keepFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const elements = [...dialog.current!.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], summary, [tabindex="0"]')]
      .filter(element => element.getClientRects().length > 0)
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
      <button type="button" autoFocus aria-label={closeLabel} onClick={onClose}>닫기 ×</button>
    </div>
    {children}
  </dialog>
}
