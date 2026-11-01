"""2단계: 대기 이벤트 → 모델 서버(가짜) 요약 → data/summaries/YYYY-MM-DD.md (계획 docs/archives/monitor-summary.md)."""

import json

import httpx
import pytest

from law_monitor import cli, secrets, summarize
from law_monitor.app import MissingSecret
from law_monitor.llm import ChatClient, LLMConfig
from law_monitor.utils.http import HttpClient
from mon_helpers import FIXED_NOW, FakeLawGo, SourceRepo, fixture, law_md, make_config, make_monitor

RULE = 'kr/소득세법/시행규칙.md'
OLD_MST, NEW_MST = '284987', '286379'
FORMS = [{'law': '소득세법 시행규칙', 'kinds': ['별표', '서식']}]
TOKEN = 'MODELTOKEN123'


class FakeLLM:
    """OpenAI 호환 /v1/chat/completions. 사용자 메시지의 '이름:' 줄을 요약에 되돌려 준다."""

    def __init__(self, *, fail: int = 0, status: int = 500, finish: str = 'stop'):
        self.fail, self.status, self.finish = fail, status, finish
        self.bodies: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/chat/completions'
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        if self.fail:
            self.fail -= 1
            return httpx.Response(self.status, json={'error': {'message': 'busy'}})
        name = next(line for line in body['messages'][-1]['content'].splitlines() if line.startswith('이름:'))
        content = f'**무엇이 바뀌었나**\n- 가짜 요약 ({name})\n\n**시행일**\n- 원문 확인 필요'
        return httpx.Response(
            200,
            json={
                'id': 'x',
                'object': 'chat.completion',
                'model': body['model'],
                'choices': [
                    {'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': self.finish}
                ],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
                'timings': {'prompt_tps': 300.0, 'generation_tps': 15.0, 'peak_memory_gb': 19.9},
            },
        )

    def client(self) -> ChatClient:
        http = HttpClient(transport=httpx.MockTransport(self.handler), retries=3, min_interval=0, sleep=lambda s: None)
        return ChatClient(LLMConfig('http://127.0.0.1:9', TOKEN, 'qwen3.8-27b-4bit'), http)


def rule_md(mst: str, promulgated: str, body: str) -> str:
    return law_md(
        '소득세법 시행규칙', {'제100조 (서식)': body}, promulgated=promulgated, effective=promulgated, mst=mst
    )


@pytest.fixture
def legalize(tmp_path):
    repo = SourceRepo(tmp_path / 'src-legalize')
    repo.commit({RULE: rule_md(OLD_MST, '2026-03-20', '서식 v1')}, '재정경제부령: 소득세법 시행규칙 (일부개정)')
    return repo


def monitor_with_events(tmp_path, legalize, llm=None):
    """법령 이벤트 1 + 별표서식 이벤트 2 (같은 개정)를 수집해 둔 Monitor."""
    lawgo = FakeLawGo({OLD_MST: fixture('law_284987'), NEW_MST: fixture('law_286379')}).client()
    config = make_config(legalize, laws=[{'name': '소득세법', 'category': 1}], forms=FORMS)
    mon = make_monitor(tmp_path, config, lawgo=lawgo, llm=llm)
    mon.bootstrap(only='legalize_law')
    legalize.commit({RULE: rule_md(NEW_MST, '2026-05-22', '서식 v2')}, '재정경제부령: 소득세법 시행규칙 (일부개정)')
    assert len(mon.collect(only='legalize_law').events) == 3
    return mon


def test_one_call_per_amendment_and_md(tmp_path, legalize):
    llm = FakeLLM()
    mon = monitor_with_events(tmp_path, legalize, llm.client())
    result = mon.summarize(run_line='collectors=1 ok=1 failed=0 events=3')

    assert result.events == 3 and result.calls == 1 and result.failed is None
    assert len(llm.bodies) == 1  # 별표서식 2건은 부모 법령 호출에 함께
    prompt = llm.bodies[0]['messages'][-1]['content']
    assert '이름: 소득세법 시행규칙' in prompt and '퇴직소득' in prompt  # 제·개정 이유
    assert (
        '서식21 ' in prompt and '서식100의2 ' in prompt
    )  # 법제처 원문과 같은 표기 (0024-02 같은 묶음 번호는 모델이 헷갈림)
    assert '0021-00' not in prompt
    assert '+[별지 제21호서식] <개정 2026. 5. 22.>' in prompt  # 서식 텍스트 diff

    assert result.path == mon.paths.home / 'data' / 'summaries' / '2026-11-02.md'
    md = result.path.read_text(encoding='utf-8')
    assert md.startswith('# 법령 변경 요약 2026-11-02')
    assert '자동 요약' in md and 'collectors=1 ok=1 failed=0 events=3' in md
    assert '## 1. 법률·시행령·시행규칙' in md and '### 소득세법 시행규칙' in md
    assert '가짜 요약 (이름: 소득세법 시행규칙' in md
    assert '바뀐 별표·서식 2개' in md and '`서식21`' in md
    assert 'data/events/2026-11-02/' in md  # 원문 이벤트 경로
    assert all(row is not None for row in mon.state.conn.execute('select processed_at from events').fetchall()[0])

    again = mon.summarize()
    assert again.events == 0 and again.calls == 0
    assert again.path.name == '2026-11-02-063000.md'  # 같은 날 두 번째 실행은 시각을 붙임
    assert '새 변경 없음' in again.path.read_text(encoding='utf-8')
    assert result.path.read_text(encoding='utf-8') == md  # 앞 파일은 그대로


def test_header_states_detection_window(tmp_path, legalize):
    """파일 날짜는 실행일. 대상은 '지난 요약(없으면 bootstrap) ~ 이번 실행' 사이에 저장소에서 새로 감지된 변경."""
    from datetime import datetime

    mon = monitor_with_events(tmp_path, legalize, FakeLLM().client())  # bootstrap·collect 모두 11-02 06:30
    first = mon.summarize().path.read_text(encoding='utf-8')
    assert '- 대상: 2026-11-02 06:30 ~ 2026-11-02 06:30 사이 새로 감지된 변경' in first
    assert '공포일과 다를 수 있음' in first

    mon.now = lambda: datetime.fromisoformat('2026-11-03T06:30:00+09:00')
    second = mon.summarize()
    assert second.path.name == '2026-11-03.md'
    assert '- 대상: 2026-11-02 06:30 ~ 2026-11-03 06:30' in second.path.read_text(encoding='utf-8')
    assert mon.state.last_run()['mode'] == 'summarize'


def test_request_shape_and_token_not_saved(tmp_path, legalize):
    llm = FakeLLM()
    mon = monitor_with_events(tmp_path, legalize, llm.client())
    result = mon.summarize()
    body = llm.bodies[0]
    assert (
        body['model'] == 'qwen3.8-27b-4bit' and body['temperature'] == 0 and body['max_tokens'] == 1200
    )  # 별표서식이 붙은 묶음
    assert body['chat_template_kwargs'] == {'enable_thinking': False} and body['stream'] is False
    assert body['messages'][0]['role'] == 'system' and '지어내지' in body['messages'][0]['content']
    assert llm.headers[0]['authorization'] == f'Bearer {TOKEN}'
    for path in [result.path, *mon.paths.logs.glob('*.log')]:
        assert TOKEN not in path.read_text(encoding='utf-8')
    assert '19.9' in next(mon.paths.logs.glob('*.log')).read_text(encoding='utf-8')  # MLX peak를 로그에


def test_truncated_summary_is_marked(tmp_path, legalize):
    mon = monitor_with_events(tmp_path, legalize, FakeLLM(finish='length').client())
    md = mon.summarize().path.read_text(encoding='utf-8')
    assert '길이 제한으로 잘림' in md


def test_lone_law_uses_smaller_limit():
    assert summarize.max_tokens_for(summarize.Group({'event_id': 'p'}, [])) == 800
    assert summarize.max_tokens_for(summarize.Group({'event_id': 'p'}, [{'event_id': 'c'}])) == 1200


def test_label_from_item_id():
    assert summarize.label({'target': {'item_id': '서식 0024-02'}}) == '서식24의2'
    assert summarize.label({'target': {'item_id': '별지 0002-00'}}) == '별지2'
    assert summarize.label({'target': {'item_id': '이상한 값'}}) == '이상한 값'
    assert summarize._title({'target': {'item_name': '삭제 &lt;2026. 3. 20.&gt;'}}) == '삭제 <2026. 3. 20.>'


def test_failure_keeps_events_pending(tmp_path, legalize):
    down = FakeLLM(fail=10**6)
    mon = monitor_with_events(tmp_path, legalize, down.client())
    result = mon.summarize()
    assert result.failed and result.calls == 0
    md = result.path.read_text(encoding='utf-8')
    assert '요약 실패' in md and '대기 이벤트 3건' in md and TOKEN not in md
    assert mon.state.pending_count() == 3
    assert len(down.bodies) == 3  # 시도 3번 (재시도 2회) 뒤 멈춤, 다음 묶음은 부르지 않음

    mon.llm = FakeLLM().client()
    later = mon.summarize()
    assert later.events == 3 and later.calls == 1 and mon.state.pending_count() == 0


def test_collector_failing_is_warning_without_model(tmp_path):
    from test_mon_runner import BrokenCollector

    llm = FakeLLM()
    mon = make_monitor(tmp_path, make_config(), collectors=[BrokenCollector()], llm=llm.client())
    for _ in range(3):
        mon.collect()
    result = mon.summarize()
    assert result.calls == 0 and llm.bodies == []
    md = result.path.read_text(encoding='utf-8')
    assert '## 경고' in md and 'broken_source' in md and '3회 연속' in md
    assert mon.state.pending_count() == 0


def test_replay_folder_summary_does_not_touch_state(tmp_path, legalize):
    llm = FakeLLM()
    lawgo = FakeLawGo({OLD_MST: fixture('law_284987'), NEW_MST: fixture('law_286379')}).client()
    config = make_config(legalize, laws=[{'name': '소득세법', 'category': 1}], forms=FORMS)
    mon = make_monitor(tmp_path, config, lawgo=lawgo, llm=llm.client())
    first = legalize.head()
    second = legalize.commit({RULE: rule_md(NEW_MST, '2026-05-22', '서식 v2')}, '개정')
    mon.replay('legalize-kr', first, second)
    folder = mon.paths.replay / f'{first[:8]}_{second[:8]}'
    result = mon.summarize(events_dir=folder)
    assert result.path == folder / 'summary.md' and result.calls == 1 and result.events == 3
    assert f'- 대상: replay 구간 `{folder.name}`' in result.path.read_text(encoding='utf-8')
    assert mon.state.pending_count() == 0 and mon.state.conn.execute('select count(*) from events').fetchone()[0] == 0


# ---- 프롬프트 길이 ----


def _event(tmp_path, eid, *, source='legalize_law', parent=None, diff='', title='서식', chunk=''):
    path = tmp_path / f'{eid}.diff'
    path.write_text(diff, encoding='utf-8')
    return {
        'event_id': eid,
        'source': source,
        'category': 1,
        'change_type': 'attachment_changed' if parent else 'law_amended',
        'target': {'law_name': '조세특례제한법 시행규칙', 'item_id': f'서식 {eid}', 'item_name': title},
        'dates': {'promulgation': '2026-03-20', 'effective': '2026-03-20'},
        'diff': {
            'path': path.name,
            'chunks': [{'article': '제1조', 'kind': 'changed', 'diff': chunk}] if chunk else [],
        },
        'context': {'parent_event_id': parent, 'amendment_reason': '이유', 'lawgo': {}},
    }


def test_prompt_is_capped_and_lists_extra_forms(tmp_path):
    parent = _event(tmp_path, 'p', chunk='\n'.join(f'+조문 줄 {i} ' + '가' * 50 for i in range(400)))
    kids = [
        _event(
            tmp_path,
            f'k{i}',
            parent='p',
            title=f'서식제목{i}',
            diff='\n'.join(f'+서식 줄 {j} ' + '나' * 60 for j in range(100)),
        )
        for i in range(46)
    ]
    prompt = summarize.build_prompt(summarize.Group(parent, kids), tmp_path)
    assert len(prompt) <= summarize.MAX_INPUT + 200
    assert '(이하 생략)' in prompt
    assert '서식제목0' in prompt and '서식제목45' in prompt  # 넘치는 서식도 제목은 나열
    assert '제목만' in prompt


def test_group_attaches_children_and_orphans():
    p = {'event_id': 'p', 'source': 'legalize_law', 'context': {}}
    c = {'event_id': 'c', 'source': 'lawgo_forms', 'context': {'parent_event_id': 'p'}}
    o = {'event_id': 'o', 'source': 'lawgo_forms', 'context': {'parent_event_id': 'gone'}}
    w = {'event_id': 'w', 'source': 'x', 'change_type': 'collector_failing', 'context': {}}
    groups, warnings = summarize.group([p, c, o, w])
    assert [(g.parent['event_id'], [k['event_id'] for k in g.children]) for g in groups] == [('p', ['c']), ('o', [])]
    assert [x['event_id'] for x in warnings] == ['w']


# ---- 설정·CLI ----


def test_llm_settings_order(tmp_path, monkeypatch):
    mon_env, root_env = tmp_path / 'monitor.env', tmp_path / 'root.env'
    root_env.write_text('LAWBOT_MODEL_SERVER=http://root\nLAWBOT_MODEL_TOKEN=roottoken\n', encoding='utf-8')
    monkeypatch.setattr(secrets, 'ENV_FILE', mon_env)
    monkeypatch.setattr(secrets, 'ROOT_ENV_FILE', root_env)
    for key in ('LAWBOT_MODEL_SERVER', 'LAWBOT_MODEL_TOKEN', 'LAW_MONITOR_SUMMARY_MODEL'):
        monkeypatch.delenv(key, raising=False)
    from law_monitor.llm import load_config

    cfg = load_config()
    assert (cfg.url, cfg.token, cfg.model) == ('http://root', 'roottoken', 'qwen3.8-27b-4bit')
    mon_env.write_text('LAWBOT_MODEL_SERVER=http://monitor\n', encoding='utf-8')
    assert load_config().url == 'http://monitor'
    monkeypatch.setenv('LAWBOT_MODEL_SERVER', 'http://env')
    assert load_config().url == 'http://env'
    root_env.write_text('', encoding='utf-8')
    monkeypatch.delenv('LAWBOT_MODEL_SERVER')
    mon_env.write_text('', encoding='utf-8')
    assert load_config() is None
    assert secrets.law_oc() is None  # LAW_OC는 레포 최상위 .env에서 읽지 않음


def test_summarize_requires_model_settings(tmp_path):
    mon = make_monitor(tmp_path, make_config(), llm=None)
    with pytest.raises(MissingSecret, match='LAWBOT_MODEL_SERVER'):
        mon.summarize()


def test_cli_daily(tmp_path, legalize, monkeypatch, capsys):
    monkeypatch.setenv('LAW_OC', 'TESTOC77')
    monkeypatch.setenv('LAWBOT_MODEL_SERVER', 'http://127.0.0.1:9')
    monkeypatch.setenv('LAWBOT_MODEL_TOKEN', TOKEN)
    cfg = tmp_path / 'targets.yaml'
    cfg.write_text(
        f'repos: {{legalize-kr: "{legalize.url}", admrule-kr: "file:///none"}}\n'
        'laws: [{name: 소득세법, category: 1}]\nadmrules: {names: [], keyword_rules: []}\n',
        encoding='utf-8',
    )
    base = ['--home', str(tmp_path / 'h'), '--config', str(cfg)]
    assert cli.main([*base, 'bootstrap', '--only', 'legalize_law']) == 0
    assert cli.main([*base, 'daily', '--only', 'legalize_law']) == 0  # 새 이벤트 없음 → 모델 호출 없음
    out = capsys.readouterr().out
    assert 'collectors=1 ok=1 failed=0 events=0' in out and 'summaries/' in out
    md = next((tmp_path / 'h' / 'data' / 'summaries').glob('*.md')).read_text(encoding='utf-8')
    assert '새 변경 없음' in md and 'collectors=1 ok=1 failed=0 events=0' in md

    monkeypatch.delenv('LAWBOT_MODEL_SERVER')
    monkeypatch.setattr(secrets, 'ENV_FILE', tmp_path / 'none.env')
    monkeypatch.setattr(secrets, 'ROOT_ENV_FILE', tmp_path / 'none.env')
    assert cli.main([*base, 'daily', '--only', 'legalize_law']) == 2  # 수집은 하고 요약은 거부
    assert 'LAWBOT_MODEL_SERVER' in capsys.readouterr().err
    runs = (
        __import__('sqlite3')
        .connect(tmp_path / 'h' / 'data' / 'state.db')
        .execute("select count(*) from runs where mode = 'normal'")
        .fetchone()[0]
    )
    assert runs == 2


def test_fixed_now_is_kst():
    assert FIXED_NOW.utcoffset().total_seconds() == 9 * 3600
