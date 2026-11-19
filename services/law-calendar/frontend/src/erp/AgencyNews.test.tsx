import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AgencyNews from './AgencyNews'

const POLICIES = [
  ['고용노동부', 'https://www.moel.go.kr/', 'https://www.moel.go.kr/site/copyright/copyrightList.do'],
  ['재정경제부', 'https://mofe.go.kr/', 'https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000'],
  ['국세청', 'https://www.nts.go.kr/', 'https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169'],
] as const

afterEach(() => vi.unstubAllGlobals())

test.each(POLICIES)('%s news names its source and links the copyright policy', async (agency, home, policy) => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    agency: 'moel', state: 'ok', officialHome: home, items: [], boards: [],
  })))
  render(<AgencyNews />)
  await userEvent.click(screen.getByRole('button', { name: agency }))
  const panel = screen.getByRole('dialog', { name: agency + ' 최신 소식' })
  expect(await within(panel).findByText(`출처: ${agency} (${home})`)).toBeInTheDocument()
  expect(within(panel).getByRole('link', { name: agency + ' 저작권정책' })).toHaveAttribute('href', policy)
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/erp-news/latest'), expect.objectContaining({ credentials: 'same-origin' }))
})

test('hometax and law center panels have no copyright policy link', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    agency: 'lawgo', state: 'ok', officialHome: 'https://www.law.go.kr/', items: [], boards: [],
  })))
  render(<AgencyNews />)
  await userEvent.click(screen.getByRole('button', { name: '홈택스' }))
  expect(screen.queryByRole('link', { name: /저작권정책/ })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '국가법령정보센터' }))
  await screen.findByRole('link', { name: '국가법령정보센터 누리집 열기 ↗' })
  expect(screen.queryByRole('link', { name: /저작권정책/ })).not.toBeInTheDocument()
})
