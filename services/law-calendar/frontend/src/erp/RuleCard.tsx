import type { ReactNode } from 'react'
import type { ErpRule, ErpRuleScope, VerifiedErpRuleVersion } from '../law-calendar/api-types'
import { kstMinute } from './data'

const APPLICABILITY: Record<ErpRuleScope['applicability'], string> = {
  legal_effective_date: '법령 시행일',
  payment_date: '지급일',
  work_period: '근로제공기간',
  income_year: '귀속연도',
  insurance_month: '보험료 적용월',
  filing_date: '신고·제출 기준일',
  other: '기타 적용 기준',
}

function applicationDate(date: string, applicability: ErpRuleScope['applicability']) {
  if (applicability === 'income_year') return date.slice(0, 4) + '년'
  if (applicability === 'insurance_month') return date.slice(0, 4) + '년 ' + Number(date.slice(5, 7)) + '월'
  return date
}

function RuleEdition({ version, applicability, current }: {
  version: VerifiedErpRuleVersion
  applicability: ErpRuleScope['applicability']
  current?: boolean
}) {
  const start = applicationDate(version.appliesFrom, applicability)
  const until = version.appliesUntil && applicationDate(version.appliesUntil, applicability)
  return <div className={current ? 'erp-rule-version' : 'erp-rule-version erp-rule-version-upcoming'}>
    <h5>{current ? '현재 유효' : '변경 예정 · 적용 시작 ' + start}</h5>
    <p className="erp-excerpt">{version.valueText}</p>
    <dl className="erp-dates">
      <div><dt>적용 기간</dt><dd>{start}부터{until && ' ' + until + '까지'}</dd></div>
      <div><dt>시행일</dt><dd>{version.effectiveFrom}</dd></div>
      <div><dt>검토 상태</dt><dd>확인 완료 · {kstMinute(version.verifiedAt)} KST</dd></div>
    </dl>
    <a href={version.evidenceUrl} target="_blank" rel="noopener noreferrer">
      {current ? '현행 공식 원문' : '변경 공식 원문'} ↗
    </a>
  </div>
}

function ScopeVersions({ scope }: { scope: ErpRuleScope }) {
  const [next, ...later] = scope.scheduledChanges
  return <div className="erp-rule-scope">
    <h4>{scope.scope}</h4>
    <p className="erp-rule-meta">{APPLICABILITY[scope.applicability]} · 조회 대상 {applicationDate(scope.targetOn, scope.applicability)}</p>
    {scope.currentVersion
      ? <RuleEdition version={scope.currentVersion} applicability={scope.applicability} current />
      : <p>현행 기준 확인 필요</p>}
    {next && <>
      <RuleEdition version={next} applicability={scope.applicability} />
      <p className="erp-rule-meta">세부 변경점은 각 판의 공식 원문에서 확인하세요.</p>
    </>}
    {scope.recheckRequired && <p className="erp-rule-meta">예정 변경 재확인 필요</p>}
    {later.length > 0 && <details className="erp-rule-details">
      <summary>이후 변경 {later.length}건</summary>
      {later.map((version) => <RuleEdition key={version.ruleVersionId} version={version} applicability={scope.applicability} />)}
    </details>}
  </div>
}

export default function RuleCard({ rule, children }: { rule: ErpRule; children?: ReactNode }) {
  const titleId = 'erp-rule-' + rule.ruleId
  return <article className="erp-card" aria-labelledby={titleId}>
    <p className="erp-tag">{rule.sourceKey}</p>
    <h3 id={titleId}>{rule.title}</h3>
    {rule.scopes.length === 0 && <p>현행 기준 확인 필요</p>}
    {rule.scopes.map((scope) => <ScopeVersions key={scope.scopeId} scope={scope} />)}
    {rule.proposalLinks.length > 0 && <div className="erp-rule-scope">
      <h4>검토 중</h4>
      <ul className="erp-links">{rule.proposalLinks.map((link) =>
        <li key={link.ruleVersionId}><a href={link.evidenceUrl} target="_blank" rel="noopener noreferrer">검토 중인 공식 원문</a></li>
      )}</ul>
    </div>}
    {children}
  </article>
}
