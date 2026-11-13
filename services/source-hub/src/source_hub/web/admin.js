'use strict'

const byId = id => document.getElementById(id)
const agencies = {
  lawgo: '국가법령정보센터', assembly: '국회', moel: '고용노동부', mofe: '재정경제부',
  nts: '국세청', hometax: '홈택스',
}
const groups = { law: '법령', erp: 'ERP' }
const statuses = {
  ready: '준비', not_tested: '미검증', disabled: '비활성', blocked: '접근 제한', manual_review: '수동 확인',
  ok: '정상', partial: '일부 성공', error: '실패',
}
let snapshot
let pending

function element(tag, text) {
  const node = document.createElement(tag)
  if (text !== undefined) node.textContent = text
  return node
}

function button(text, onClick, pressed) {
  const node = element('button', text)
  node.type = 'button'
  node.addEventListener('click', onClick)
  if (pressed !== undefined) node.setAttribute('aria-pressed', String(pressed))
  return node
}

async function request(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    const body = await response.text()
    let message = body
    if (response.headers.get('content-type')?.includes('json')) message = JSON.parse(body).detail
    throw new Error(typeof message === 'string' && message ? message : '요청에 실패했습니다.')
  }
  return response.json()
}

function optionList(select, values, label) {
  const selected = select.value
  select.replaceChildren(new Option(select.options[0].text, ''), ...values.map(value => new Option(label(value), value)))
  select.value = values.includes(selected) ? selected : ''
}

function renderSources() {
  const { value: agency } = byId('agency')
  const { value: topic } = byId('topic')
  const query = byId('query').value.trim().toLocaleLowerCase('ko')
  const rows = snapshot.sources.filter(source =>
    (!agency || source.agency === agency) &&
    (!topic || source.category_tags.includes(topic)) &&
    [source.board_name, source.source_id, ...source.category_tags].some(value => value.toLocaleLowerCase('ko').includes(query)))
  byId('sources').replaceChildren(...rows.map(source => {
    const row = element('tr')
    const label = element('td', source.board_name)
    label.append(element('small', agencies[source.agency] + ' · ' + source.source_id))
    const status = element('td', statuses[source.status] ?? source.status)
    if (!source.source_url) status.append(element('small', '공식 수집 경로 확인 필요'))
    const last = element('td', source.last_success_at ?? '성공 기록 없음')
    if (source.last_attempt_at) last.append(element('small', '시도 ' + source.last_attempt_at))
    if (source.error) last.append(element('small', source.error))
    if (source.new_count !== null) last.append(element('small', `신규 ${source.new_count}건 · 수정 ${source.modified_count}건`))
    const edit = element('td')
    edit.append(button(source.board_name + ' 설정', () => editSource(source)))
    if (source.last_attempt_at) {
      const history = element('details')
      history.append(element('summary', '최근 실행'))
      history.addEventListener('toggle', async () => {
        if (!history.open || history.childElementCount > 1) return
        try {
          const runs = await request('/api/collector/sources/' + encodeURIComponent(source.source_id) + '/runs')
          const list = element('ul')
          for (const run of runs) {
            list.append(element('li', `${run.started_at} · ${statuses[run.outcome]}` + (run.error ? ' · ' + run.error : '')))
          }
          history.append(list)
        } catch (error) {
          history.append(element('p', error.message))
        }
      })
      edit.append(history)
    }
    row.append(label, element('td', source.mode), status, last, element('td', source.next_run_at ?? '일정 연결 전'), edit)
    return row
  }))
  if (!rows.length) {
    const row = element('tr')
    const cell = element('td', '조건에 맞는 수집원이 없습니다.')
    cell.colSpan = 6
    row.append(cell)
    byId('sources').append(row)
  }
}

function toggle(kind, id, name, enabled) {
  const path = `/api/collector/settings/${kind}/${encodeURIComponent(id)}`
  return button(name + (enabled ? ' 켜짐' : ' 꺼짐'), () => {
    openEditor(name, path, () => ({ enabled: !enabled }), enabled)
  }, enabled)
}

function renderScope() {
  byId('switches').replaceChildren(
    ...Object.entries(snapshot.groups).map(([id, enabled]) => toggle('group', id, groups[id], enabled)),
    ...Object.entries(snapshot.agencies).map(([id, enabled]) => toggle('agency', id, agencies[id], enabled)),
  )
  const laws = snapshot.sources.find(source => source.source_id === 'legalize_law')?.laws ?? []
  byId('laws').replaceChildren(...laws.map(law => {
    const detail = element('details')
    detail.append(element('summary', law.name))
    detail.append(toggle('law', law.law_id, law.name, law.enabled))
    for (const part of law.parts) detail.append(toggle('law-part', part.id, part.name, part.enabled))
    return detail
  }))
}

async function reload() {
  try {
    const [sources, audit] = await Promise.all([request('/api/collector/sources'), request('/api/collector/audit')])
    snapshot = sources
    optionList(byId('agency'), Object.keys(agencies), id => agencies[id])
    optionList(byId('topic'), [...new Set(snapshot.sources.flatMap(source => source.category_tags))].sort(), value => value)
    renderScope()
    renderSources()
    byId('audit').replaceChildren(...audit.map(record => {
      const row = element('li', `${record.at} · ${record.actor} · ${record.target} · ${record.reason}`)
      const details = element('details')
      details.append(element('summary', '이전 값과 새 값'), element('pre', JSON.stringify({ 이전: record.old, 변경: record.new }, null, 2)))
      row.append(details)
      return row
    }))
    byId('status').textContent = `수집원 ${snapshot.sources.length}개 · 설정 버전 ${snapshot.config_version}`
  } catch (error) {
    byId('status').textContent = error.message
  }
}

function openEditor(title, path, collect, disables, fields = [], method = 'PATCH') {
  pending = { path, collect, method, version: snapshot.config_version, trigger: document.activeElement }
  byId('editor-title').textContent = title
  byId('effect').textContent = disables
    ? '다음 통합 실행부터 새 수집을 중지합니다. 과거 공개 자료와 이력은 보존하고 기본 일일 알림에서 제외합니다.'
    : '변경 사항은 다음 통합 실행부터 적용됩니다.'
  byId('fields').replaceChildren(...fields)
  byId('reason').value = ''
  byId('edit-status').textContent = ''
  byId('editor').showModal()
  byId('reason').focus()
}

function field(label, value, options) {
  const input = options ? element('select') : element('input')
  if (options) input.append(...options.map(option => new Option(option, option)))
  input.value = value ?? ''
  const wrapper = element('label', label)
  wrapper.append(input)
  return { wrapper, input }
}

function editSource(source) {
  const creating = !source
  const sourceId = field('고정 수집원 ID', '')
  const agency = field('기관 ID', 'moel', Object.keys(agencies))
  const group = field('그룹', 'erp', Object.keys(groups))
  const title = field('게시판 이름', source?.board_name)
  const homepage = field('공식 홈 URL', source?.official_homepage)
  const url = field('공식 수집 URL', source?.source_url)
  const mode = field('수집 모드', source?.mode ?? 'html', ['legacy', 'local', 'api', 'rss', 'html', 'browser', 'metadata'])
  const schedule = field('수집 간격(분)', source?.interval_minutes ?? 1440)
  schedule.input.type = 'number'
  const reconcile = field('목록 대조 간격(분)', source?.reconcile_interval_minutes ?? 10080)
  reconcile.input.type = 'number'
  const backfill = field('백필(일)', source?.backfill_days ?? 30)
  backfill.input.type = 'number'
  const tags = field('업무 분야(쉼표로 구분)', source?.category_tags.join(', '))
  const policy = field('공개 검토', source?.review_policy ?? 'manual', ['manual', 'auto_metadata'])
  const rights = field('이용 조건', source?.license_state ?? 'unverified', ['unverified', 'links_only', 'approved'])
  const enabled = field('활성화', '')
  enabled.input.type = 'checkbox'
  enabled.input.checked = source?.enabled ?? false
  const fields = [
    ...(creating ? [sourceId, agency, group] : []), title, homepage, url, mode, schedule,
    reconcile, backfill, tags, policy, rights, enabled,
  ].map(entry => entry.wrapper)
  const collect = () => {
    const data = {
      board_name: title.input.value.trim(),
      official_homepage: homepage.input.value.trim(),
      source_url: url.input.value.trim() || null,
      mode: mode.input.value,
      interval_minutes: schedule.input.valueAsNumber,
      reconcile_interval_minutes: reconcile.input.valueAsNumber,
      backfill_days: backfill.input.valueAsNumber,
      category_tags: tags.input.value.split(',').map(tag => tag.trim()).filter(Boolean),
      review_policy: policy.input.value,
      license_state: rights.input.value,
      enabled: enabled.input.checked,
    }
    if (creating) {
      pending.path = '/api/collector/sources/' + encodeURIComponent(sourceId.input.value.trim())
      return { ...data, group: group.input.value, agency: agency.input.value, adapter: agency.input.value }
    }
    return Object.fromEntries(Object.entries(data).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(source[key])))
  }
  openEditor(source?.board_name ?? '수집원 등록', '/api/collector/sources/' + (source?.source_id ?? ''),
    collect, false, fields, creating ? 'POST' : 'PATCH')
  enabled.input.addEventListener('change', () => {
    byId('effect').textContent = enabled.input.checked
      ? '다음 통합 실행부터 설정이 적용됩니다. 미검증 소스는 점검이 필요합니다.'
      : '다음 통합 실행부터 새 수집을 중지합니다. 과거 자료는 보존하고 기본 일일 알림에서 제외합니다.'
  })
}

byId('save').addEventListener('click', async () => {
  const reason = byId('reason').value.trim()
  if (!reason) {
    byId('edit-status').textContent = '변경 사유를 입력하세요.'
    return
  }
  const changes = pending.collect()
  if (!Object.keys(changes).length) {
    byId('editor').close()
    return
  }
  const { path, method, version } = pending
  byId('save').disabled = true
  byId('cancel').disabled = true
  try {
    await request(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version, reason, changes }) })
    byId('editor').close()
    await reload()
    byId('reload').focus()
  } catch (error) {
    byId('edit-status').textContent = error.message
  } finally {
    byId('save').disabled = false
    byId('cancel').disabled = false
  }
})

byId('editor').addEventListener('cancel', event => {
  if (byId('save').disabled) event.preventDefault()
})
byId('editor').addEventListener('close', () => pending?.trigger?.focus())
byId('cancel').addEventListener('click', () => byId('editor').close())
byId('add').addEventListener('click', () => { if (snapshot) editSource() })
byId('reload').addEventListener('click', reload)
for (const id of ['agency', 'topic', 'query']) {
  byId(id).addEventListener('input', () => { if (snapshot) renderSources() })
}
reload()
