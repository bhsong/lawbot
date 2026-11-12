import type { Calculation, CalculationCheck } from '../types'

const numberFormat = new Intl.NumberFormat('ko-KR')
const NOT_ENTERED = '미입력(0)'

function formatValue(value: number | string, unit?: string) {
  return typeof value === 'number' ? `${numberFormat.format(value)}${unit ?? '원'}` : value + (unit ?? '')
}

export function CalculationCard({ calculation, check }: { calculation: Calculation; check?: CalculationCheck }) {
  if (calculation.status !== 'calculated') {
    return (
      <section className="calculation" aria-label="계산 안내">
        <h2>계산 안내</h2>
        <p>{calculation.message ?? '이 질문에 사용할 수 있는 계산기가 없습니다.'}</p>
        {calculation.articles && calculation.articles.length > 0 && (
          <ul className="calculation-articles" aria-label="계산 근거 조문">
            {calculation.articles.map((article) => (
              <li key={article}>{article}</li>
            ))}
          </ul>
        )}
      </section>
    )
  }

  const { year, inputs = [], steps = [], results = [] } = calculation
  const omitted = inputs.filter(({ source }) => source === NOT_ENTERED).length
  const conditional = results.some(({ condition }) => condition)
  const valueLabel = results.some(({ unit }) => unit === '일') ? '일수' : '금액'
  const tax = calculation.calculator === 'medical_expense_credit' || calculation.calculator === 'midyear_income_tax'
  return (
    <section className="calculation" aria-label="계산 결과">
      <header>
        <h2>계산 결과</h2>
        {year !== undefined && <span>{year}년 {tax ? '귀속' : '기준'}</span>}
      </header>
      <dl className="calculation-results">
        {results.map(({ label, value, unit, condition }) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <strong>{formatValue(value, unit)}</strong>
              {condition && <span className="calculation-condition">{condition}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {conditional && calculation.message && <p>{calculation.message}</p>}
      <details>
        <summary>
          <span>입력과 계산 단계</span>
          {omitted > 0 && <span className="calculation-omitted">{NOT_ENTERED} {omitted}건</span>}
        </summary>
        <table>
          <caption>계산 입력</caption>
          <thead>
            <tr>
              <th scope="col">항목</th>
              <th scope="col">값</th>
              <th scope="col">출처</th>
            </tr>
          </thead>
          <tbody>
            {inputs.map(({ label, value, source, unit }) => (
              <tr key={label} className={source === NOT_ENTERED ? 'calculation-omitted' : undefined}>
                <th scope="row">{label}</th>
                <td>{formatValue(value, unit)}</td>
                <td>{source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table>
          <caption>단계별 계산</caption>
          <thead>
            <tr>
              <th scope="col">항목</th>
              <th scope="col">식</th>
              <th scope="col">값</th>
              <th scope="col">근거 조문</th>
            </tr>
          </thead>
          <tbody>
            {steps.map(({ label, formula, value, article, unit }) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{formula}</td>
                <td>{formatValue(value, unit)}</td>
                <td>{article}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {check === 'mismatch' && (
        <p className="error" role="alert">
          답변 결론의 {valueLabel}이 계산 결과와 다릅니다. 계산 표를 기준으로 확인해 주세요.
        </p>
      )}
      {check === 'missing' && (
        <p className="error" role="alert">
          답변 결론에서 {valueLabel}을 확인할 수 없습니다. 계산 표를 기준으로 확인해 주세요.
        </p>
      )}
    </section>
  )
}
