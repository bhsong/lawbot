import { ChangeView, Modal, dateRoleLabel } from '@lawbot/ui'
import type { CalendarChange } from '@lawbot/ui'

const titleId = 'lc-detail-title'
const descriptionId = 'lc-detail-notice'

export function ChangeDialog({ change, demo, onClose }: { change: CalendarChange; demo: boolean; onClose: () => void }) {
  const header = change.assembly ? dateRoleLabel(change.assembly.dateRole)
    : change.kind === 'notice' ? '입법예고 · 게시일 기준' : '시행 조문 · 시행일 기준'
  return <Modal header={header} closeLabel="상세 닫기" labelledBy={titleId} describedBy={descriptionId} onClose={onClose}>
    <ChangeView change={change} demo={demo} titleId={titleId} descriptionId={descriptionId} />
  </Modal>
}
