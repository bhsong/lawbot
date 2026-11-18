import { ChangeView, Modal } from '@lawbot/ui'
import type { CalendarChange } from '@lawbot/ui'

const titleId = 'lc-detail-title'
const descriptionId = 'lc-detail-description'

export function ChangeDialog({ change, demo, onClose }: { change: CalendarChange; demo: boolean; onClose: () => void }) {
  return <Modal header="시행 조문 · 시행일 기준" closeLabel="상세 닫기" labelledBy={titleId} describedBy={descriptionId} onClose={onClose}>
    <ChangeView change={change} demo={demo} titleId={titleId} descriptionId={descriptionId} />
  </Modal>
}
