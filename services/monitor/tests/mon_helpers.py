"""테스트 도우미: 임시 git 저장소(legalize-kr·admrule-kr 모양)와 설정을 만든다.

conftest.py를 두지 않는다: 다른 서비스의 tests/conftest.py와 모듈 이름이 겹치기 때문 (루트 pytest가 함께 수집).
"""

import copy
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import httpx

from law_monitor.app import Monitor
from law_monitor.config import from_dict
from law_monitor.lawgo import LawGoClient
from law_monitor.paths import Paths
from law_monitor.utils.http import HttpClient

FIXED_NOW = datetime.fromisoformat('2026-11-02T06:30:00+09:00')


def git(cwd: Path, *args: str, env: dict | None = None) -> str:
    full_env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', **(env or {})}
    out = subprocess.run(['git', *args], cwd=cwd, env=full_env, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def law_md(
    title: str,
    articles: dict[str, str],
    *,
    promulgated: str = '2026-01-01',
    effective: str = '2026-01-01',
    status: str = '시행',
    attachments: list[dict] | None = None,
    addenda: list[str] | None = None,
    mst: str | None = None,
) -> str:
    """legalize-kr 법령 파일 모양: 머리말(YAML) + # 제목 + ##### 제N조 + ## 부칙. mst가 있으면 법제처 보강 대상."""
    lines = [
        '---',
        f'제목: {title}',
        *([f'법령MST: {mst}'] if mst else []),
        "법령ID: '000001'",
        '소관부처:',
        '- 고용노동부',
    ]
    lines += [f'공포일자: {promulgated}', "공포번호: '00001'", f'시행일자: {effective}', f'상태: {status}']
    lines += [f'출처: https://www.law.go.kr/법령/{title.replace(" ", "")}']
    lines += _attachments_yaml(attachments)
    lines += ['---', '', f'# {title}', '', '## 제1장 총칙', '']
    for key, text in articles.items():
        lines += [f'##### {key}', '', text, '']
    lines += ['## 부칙', '']
    for block in addenda or ['부칙 <제1호,2000.1.1>\n\n제1조 (시행일) 이 법은 공포한 날부터 시행한다.']:
        lines += [block, '', '']
    return '\n'.join(lines)


def admrule_md(
    rule_id: str,
    name: str,
    agency: str,
    body: str,
    *,
    issued: str = '2026-01-01',
    effective: str = '2026-01-01',
    number: str = '2026-1',
    attachments: list[dict] | None = None,
    serial: str | None = None,
) -> str:
    """admrule-kr 본문.md 모양. serial(행정규칙일련번호)은 법제처 상세 조회 키."""
    lines = [
        '---',
        f"행정규칙ID: '{rule_id}'",
        *([f"행정규칙일련번호: '{serial}'"] if serial else []),
        f"행정규칙명: '{name}'",
    ]
    lines += ["행정규칙종류: '고시'", f"소관부처명: '{agency}'", f"발령번호: '{number}'"]
    lines += [f'발령일자: {issued}', f'시행일자: {effective}', "제개정구분: '일부개정'", "현행여부: 'Y'"]
    lines += [f"출처: 'https://www.law.go.kr/행정규칙/{name.replace(' ', '')}'"]
    lines += _attachments_yaml(attachments)
    lines += ['---', '', f'# {name}', '', '##### 제1조 (목적)', '', body, '']
    return '\n'.join(lines)


def attachment(number: str, title: str, *, kind: str = '서식', branch: str = '00', seq: int = 1) -> dict:
    return {
        '별표번호': number,
        '별표가지번호': branch,
        '별표구분': kind,
        '제목': title,
        '파일링크': f'https://www.law.go.kr/LSW/flDownload.do?flSeq={seq}',
        'PDF링크': f'https://www.law.go.kr/LSW/flDownload.do?flSeq={seq + 1}',
    }


def _attachments_yaml(items: list[dict] | None) -> list[str]:
    if not items:
        return ['첨부파일: []']
    out = ['첨부파일:']
    for item in items:
        first = True
        for key, value in item.items():
            out.append(f"{'- ' if first else '  '}{key}: '{value}'")
            first = False
    return out


class SourceRepo:
    """원격 역할을 하는 일반 git 저장소. 커밋 날짜를 마음대로 정할 수 있다 (legalize-kr은 커밋 날짜가 공포일)."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        git(path, 'init', '-q', '-b', 'main')
        git(path, 'config', 'user.name', 'test')
        git(path, 'config', 'user.email', 'test@example.com')
        git(path, 'config', 'uploadpack.allowFilter', 'true')  # 부분 클론(--filter=blob:none) 경로를 그대로 시험
        git(path, 'config', 'uploadpack.allowAnySHA1InWant', 'true')

    @property
    def url(self) -> str:
        return self.path.as_uri()

    def commit(self, files: dict[str, str | None], message: str, date: str = '2026-01-01T12:00:00+09:00') -> str:
        """files: 경로 → 내용 (None이면 삭제). 커밋 해시를 돌려준다."""
        for rel, text in files.items():
            target = self.path / rel
            if text is None:
                git(self.path, 'rm', '-q', rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
            git(self.path, 'add', rel)
        env = {'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date}
        git(self.path, 'commit', '-q', '--allow-empty', '-m', message, env=env)
        return self.head()

    def move(self, old: str, new: str, message: str) -> str:
        (self.path / new).parent.mkdir(parents=True, exist_ok=True)
        git(self.path, 'mv', old, new)
        git(self.path, 'commit', '-q', '-m', message)
        return self.head()

    def head(self) -> str:
        return git(self.path, 'rev-parse', 'HEAD')

    def rewrite_history(self, message: str = '재구성') -> str:
        """force-push 흉내: 지금 내용 그대로 부모 없는 커밋 하나로 main을 바꾼다 (모든 커밋 해시가 바뀜)."""
        git(self.path, 'checkout', '-q', '--orphan', 'rebuilt')
        git(self.path, 'commit', '-q', '-m', message)
        git(self.path, 'branch', '-q', '-M', 'rebuilt', 'main')
        return self.head()


def make_config(
    legalize: SourceRepo | None = None,
    admrule: SourceRepo | None = None,
    *,
    laws: list[dict] | None = None,
    names: list[dict] | None = None,
    keyword_rules: list[dict] | None = None,
    forms: list[dict] | None = None,
):
    data = {
        'repos': {
            'legalize-kr': legalize.url if legalize else 'file:///nonexistent/legalize-kr',
            'admrule-kr': admrule.url if admrule else 'file:///nonexistent/admrule-kr',
        },
        'laws': laws
        if laws is not None
        else [{'name': '근로기준법', 'category': 1}, {'name': '최저임금법', 'category': 7}],
        'forms': forms or [],
        'admrules': {
            'names': names
            if names is not None
            else [
                {'agency': '고용노동부', 'pattern': r'^\d{4}년 적용 최저임금(안)? 고시$', 'category': 7},
            ],
            'keyword_rules': keyword_rules
            if keyword_rules is not None
            else [
                {'agency': '국세청', 'keywords': ['원천징수', '지급명세서'], 'category': 3},
            ],
        },
        'prelegislation': {'keywords': ['근로기준법']},
    }
    return from_dict(data)


def make_monitor(tmp_path: Path, config, *, collectors=None, now=FIXED_NOW, lawgo=None) -> Monitor:
    """lawgo가 없으면 빈 가짜 법제처 서버 (머리말에 법령MST·일련번호가 없는 파일은 호출 자체가 없음)."""
    client = lawgo if lawgo is not None else FakeLawGo().client()
    return Monitor(Paths(tmp_path / 'home'), config, collectors=collectors, now=lambda: now, lawgo=client)


# ---- 가짜 법제처 서버 ----

FIXTURES = Path(__file__).parent / 'fixtures' / 'lawgo'
TEST_OC = 'TESTOC77'
PDF, HWP = b'%PDF-1.4 fake ', b'\xd0\xcf\x11\xe0 fake '


def fixture(name: str) -> dict:
    """스파이크(docs/evals/2026-11-01-monitor-spike.md) 응답을 줄인 견본."""
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def units_of(body: dict) -> list[dict]:
    return body[next(iter(body))]['별표']['별표단위']


def modified(body: dict, edit) -> dict:
    out = copy.deepcopy(body)
    edit(out[next(iter(out))])
    return out


class FakeLawGo:
    """httpx.MockTransport 처리기. 법령은 MST, 행정규칙은 ID(일련번호)로 응답한다. 요청은 모두 기록한다."""

    def __init__(
        self,
        laws: dict | None = None,
        admrules: dict | None = None,
        *,
        fail: int = 0,
        status: int = 503,
        html_downloads: bool = False,
    ):
        self.laws, self.admrules = laws or {}, admrules or {}
        self.fail, self.status, self.html_downloads = fail, status, html_downloads
        self.requests: list[httpx.Request] = []
        self.hwp_seqs = set()
        for body in [*self.laws.values(), *self.admrules.values()]:
            for u in units_of(body) if '별표' in body[next(iter(body))] else []:
                self.hwp_seqs.add(u.get('별표서식파일링크', '').rsplit('=', 1)[-1])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            self.fail -= 1
            return httpx.Response(self.status, text='busy')
        params = request.url.params
        if request.url.path == '/DRF/lawService.do':
            store, key = (self.laws, 'MST') if params.get('target') == 'law' else (self.admrules, 'ID')
            body = store.get(params.get(key))
            return httpx.Response(200, json=body if body is not None else {'result': '일치하는 법령이 없습니다'})
        if request.url.path.endswith('/flDownload.do'):
            seq = params.get('flSeq', '')
            if self.html_downloads:
                return httpx.Response(200, text='<html>오류</html>', headers={'Content-Type': 'text/html'})
            return httpx.Response(200, content=(HWP if seq in self.hwp_seqs else PDF) + seq.encode())
        return httpx.Response(404)

    def client(self, oc: str = TEST_OC) -> LawGoClient:
        http = HttpClient(transport=httpx.MockTransport(self.handler), min_interval=0, sleep=lambda s: None)
        return LawGoClient(oc, http)

    def count(self, *, path: str, **params) -> int:
        return sum(
            1
            for r in self.requests
            if r.url.path.endswith(path) and all(r.url.params.get(k) == v for k, v in params.items())
        )


LAW_PATH = 'kr/근로기준법/법률.md'
WAGE_PATH = 'kr/최저임금법/법률.md'
