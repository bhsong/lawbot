import type { Calculation, CalculationCheck } from '../types'

const numberFormat = new Intl.NumberFormat('ko-KR')
const NOT_ENTERED = '미입력(0)'

function formatValue(value: number | string) {
  return typeof value === 'number' ? `${numberFormat.format(value)}원` : value
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
  return (
    <section className="calculation" aria-label="계산 결과">
      <header>
        <h2>계산 결과</h2>
        {year !== undefined && <span>{year}년 귀속</span>}
      </header>
      <dl className="calculation-results">
        {results.map(({ label, value }) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <strong>{formatValue(value)}</strong>
            </dd>
          </div>
        ))}
      </dl>
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
            {inputs.map(({ label, value, source }) => (
              <tr key={label} className={source === NOT_ENTERED ? 'calculation-omitted' : undefined}>
                <th scope="row">{label}</th>
                <td>{formatValue(value)}</td>
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
            {steps.map(({ label, formula, value, article }) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{formula}</td>
                <td>{formatValue(value)}</td>
                <td>{article}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {check === 'mismatch' && (
        <p className="error" role="alert">
          답변 결론의 금액이 계산 결과와 다릅니다. 계산 표를 기준으로 확인해 주세요.
        </p>
      )}
      {check === 'missing' && (
        <p className="error" role="alert">
          답변 결론에서 금액을 확인할 수 없습니다. 계산 표를 기준으로 확인해 주세요.
        </p>
      )}
    </section>
  )
}
