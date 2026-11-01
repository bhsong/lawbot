"""2단계: 대기 이벤트를 모델로 요약해 하루치 md를 만든다 (계획 docs/archives/monitor-summary.md).

- 법령·행정규칙 이벤트 하나 = 모델 호출 하나. 같은 개정의 별표서식 이벤트(context.parent_event_id)는 그 호출에 함께
- collector_failing은 모델 없이 경고로
- 모델이 실패하면 거기서 멈추고, 요약하지 못한 이벤트는 processed_at을 비워 둔다 (다음 실행에서 다시)
"""

import html
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from law_monitor.llm import ChatClient, LLMError

log = logging.getLogger('law_monitor.summarize')

MAX_INPUT = 12_000  # 한 호출의 사용자 메시지 최대 글자 수 (27B 메모리·시간)
REASON_LIMIT, AMENDMENT_LIMIT, ARTICLE_LIMIT, FORM_LIMIT = 3_000, 2_000, 5_000, 1_500
TITLE_RESERVE = 2_000  # 길이 때문에 내용을 못 넣은 별표서식의 제목 목록 자리
LIST_LIMIT = 15  # md에 나열할 별표서식 수
MAX_TOKENS, MAX_TOKENS_WITH_FORMS = 800, 1200  # 서식이 붙으면 요약이 길어짐 (실제 확인: 서식 46개에서 800토큰에 잘림)

SYSTEM = """너는 ERP(급여 계산·원천징수·4대보험·부가가치세 신고) 담당 개발자에게 법령 변경을 알려 주는 요약가다.
아래 규칙을 지켜 한국어로 요약한다.
- 주어진 원문(제·개정 이유, 개정문, 바뀐 조문·서식 diff)에 있는 내용만 쓴다. 원문에 없는 금액·날짜·조문을 지어내지 않는다.
- 조문 번호, 서식 번호, 금액, 날짜는 원문 그대로 쓴다.
- 원문만으로 판단하기 어려우면 "원문 확인 필요"라고 쓴다.
- 적용 대상·기준일(예: "언제 이후 퇴직하는 사람")은 원문에 그 표현이 있을 때만 쓰고, 추정하지 않는다.
- 서식 번호는 입력에 적힌 표기(예: 서식21, 서식24의2) 그대로 쓴다. 별지 제24호서식(2)처럼 괄호 번호는 같은 서식의 다른 쪽이다.
- 별표·서식이 많으면 시행일 항목에는 날짜별 개수와 대표 서식 몇 개만 쓰고 번호를 모두 나열하지 않는다.
- 다음 네 항목만, 각 항목은 짧은 글머리표로 쓴다:
**무엇이 바뀌었나** (2~4줄)
**시행일** (조문·서식별 시행일이 다르면 나눠서)
**ERP 영향** (급여 계산, 원천징수, 신고 서식, 보험료율 중 해당하는 것. 없으면 "직접 영향 적음")
**확인할 것**"""

CATEGORY_TITLES = {
    1: '법률·시행령·시행규칙',
    2: '별표·서식',
    3: '행정규칙',
    4: '입법예고·법안',
    5: '전자신고 파일 레이아웃',
    6: '정기 리마인드',
    7: '4대보험·최저임금',
}
CHANGE_NAMES = {
    'law_amended': '개정',
    'law_new': '신규',
    'law_repealed': '폐지',
    'admrule_amended': '개정',
    'admrule_new': '신규',
    'attachment_changed': '바뀜',
    'attachment_added': '신설',
    'attachment_removed': '삭제',
}
PARENT_SOURCES = ('legalize_law', 'legalize_admrule')


@dataclass
class Group:
    parent: dict | None
    children: list[dict] = field(default_factory=list)

    @property
    def head(self) -> dict:
        return self.parent if self.parent is not None else self.children[0]

    @property
    def events(self) -> list[dict]:
        return ([self.parent] if self.parent is not None else []) + self.children


@dataclass
class SummaryResult:
    path: Path
    events: int
    calls: int
    failed: str | None
    pending: int


def group(events: list[dict]) -> tuple[list[Group], list[dict]]:
    """부모(법령·행정규칙) 아래에 별표서식을 붙인다. 부모가 없는 별표서식은 혼자 한 묶음, collector_failing은 경고."""
    warnings = [e for e in events if e.get('change_type') == 'collector_failing']
    rest = [e for e in events if e.get('change_type') != 'collector_failing']
    by_id = {e['event_id']: Group(e) for e in rest if e.get('source') in PARENT_SOURCES}
    groups = list(by_id.values())
    for e in rest:
        if e.get('source') in PARENT_SOURCES:
            continue
        parent = by_id.get((e.get('context') or {}).get('parent_event_id'))
        if parent is not None:
            parent.children.append(e)
        else:
            groups.append(Group(e))  # 부모는 이미 요약됨 (지난 실행) → 서식만 따로
    return groups, warnings


def _changed_lines(text: str) -> str:
    return '\n'.join(line for line in text.splitlines() if line[:1] in '+-' and not line.startswith(('+++', '---')))


def _diff_file(event: dict, home: Path) -> str:
    rel = (event.get('diff') or {}).get('path')
    path = home / rel if rel else None
    return path.read_text(encoding='utf-8') if path and path.exists() else ''


def _cut(text: str | None, limit: int) -> str:
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit].rstrip() + '\n(이하 생략)'


def label(event: dict) -> str:
    """'서식 0024-02' → '서식24의2' (법제처 원문 표기). 모양이 다르면 그대로."""
    item = str((event.get('target') or {}).get('item_id') or '')
    kind, _, rest = item.partition(' ')
    number, _, branch = rest.partition('-')
    if not (kind and number.isdigit() and branch.isdigit()):
        return item
    return f'{kind}{int(number)}' + (f'의{int(branch)}' if int(branch) else '')


def max_tokens_for(g: Group) -> int:
    return MAX_TOKENS_WITH_FORMS if g.children else MAX_TOKENS


def _title(event: dict) -> str:
    t = event.get('target') or {}
    name = html.unescape(str(t.get('item_name') or ''))  # 법제처 제목에 &lt; 같은 문자가 남아 있음
    return name if len(name) <= 60 else name[:60] + '…'


def build_prompt(g: Group, home: Path) -> str:
    head = g.head
    t, d = head.get('target') or {}, head.get('dates') or {}
    ctx = head.get('context') or {}
    lawgo = ctx.get('lawgo') or {}
    lines = [
        f'이름: {t.get("law_name")}',
        f'변경: {CHANGE_NAMES.get(head.get("change_type"), head.get("change_type"))}'
        + (' (삭제·폐지)' if head.get('removed') else ''),
        f'공포일: {d.get("promulgation")} / 시행일: {d.get("effective")}',
    ]
    if lawgo.get('article_effective'):
        lines.append(f'조문별 시행일(법제처 원문): {lawgo["article_effective"]}')
    if lawgo.get('attachment_effective'):
        lines.append(f'서식별 시행일(법제처 원문): {lawgo["attachment_effective"]}')
    body = '\n'.join(lines)
    body += '\n\n[제·개정 이유]\n' + (_cut(ctx.get('amendment_reason'), REASON_LIMIT) or '(법제처 원문 없음)')
    if ctx.get('amendment_text'):
        body += '\n\n[개정문 앞부분]\n' + _cut(ctx['amendment_text'], AMENDMENT_LIMIT)
    if g.parent is not None:
        chunks = (g.parent.get('diff') or {}).get('chunks') or []
        if chunks:
            articles = ''
            for c in chunks:
                kind = CHANGE_NAMES.get('attachment_' + str(c.get('kind')), c.get('kind'))
                articles += f'\n#### {c.get("article")} ({kind})\n' + _changed_lines(c.get('diff') or '')
            body += '\n\n[바뀐 조문]' + _cut(articles, ARTICLE_LIMIT)
    kids = g.children if g.parent is not None else g.events
    if kids:
        body += f'\n\n[바뀐 별표·서식 {len(kids)}개]'
    titles_only = []
    for k in kids:
        kd = k.get('dates') or {}
        header = (
            f'\n#### {label(k)} {_title(k)} '
            f'({CHANGE_NAMES.get(k.get("change_type"), k.get("change_type"))}, 시행 {kd.get("effective")})\n'
        )
        part = header + _cut(_changed_lines(_diff_file(k, home)), FORM_LIMIT)
        if len(body) + len(part) > MAX_INPUT - TITLE_RESERVE:
            titles_only.append(k)
            continue
        body += part
    titles = ''
    if titles_only:
        titles = f'\n\n[길이 때문에 제목만 넣은 별표·서식 {len(titles_only)}개]\n' + '\n'.join(
            f'- {label(k)} {_title(k)}' for k in titles_only
        )
        titles = _cut(titles, TITLE_RESERVE)
    return _cut(body, MAX_INPUT - len(titles)) + titles


def _section(g: Group) -> int:
    return int(g.head.get('category') or 99)


def _render(g: Group, text: str | None, paths: dict[str, str], truncated: bool = False) -> str:
    head = g.head
    t, d = head.get('target') or {}, head.get('dates') or {}
    kind = CHANGE_NAMES.get(head.get('change_type'), head.get('change_type'))
    out = f'### {t.get("law_name")} ({kind}, 공포 {d.get("promulgation")}, 시행 {d.get("effective")})\n\n'
    out += (text or '(요약 없음)') + '\n'
    if truncated:
        out += '\n(요약이 길이 제한으로 잘림: 원문을 확인하세요)\n'
    kids = g.children if g.parent is not None else g.events
    if kids:
        shown = ', '.join(
            f'`{label(k)}` {_title(k)} (시행 {(k.get("dates") or {}).get("effective")})' for k in kids[:LIST_LIMIT]
        )
        more = f' 외 {len(kids) - LIST_LIMIT}개' if len(kids) > LIST_LIMIT else ''
        out += f'\n- 바뀐 별표·서식 {len(kids)}개: {shown}{more}\n'
    source = (head.get('context') or {}).get('source_url')
    out += f'- 원문: `{paths.get(head["event_id"], "")}`' + (f' · [법제처]({source})' if source else '') + '\n'
    return out


def write_summary(
    events: list[dict],
    paths: dict[str, str],
    *,
    home: Path,
    client: ChatClient,
    out_path: Path,
    title_date: str,
    run_line: str | None,
    ran_at: str,
    window: str | None = None,
) -> tuple[SummaryResult, list[str]]:
    """요약 md를 쓰고 (결과, 요약을 끝낸 event_id 목록)을 돌려준다."""
    groups, warnings = group(events)
    groups.sort(key=lambda g: (_section(g), str((g.head.get('target') or {}).get('law_name'))))
    rendered: list[tuple[int, str]] = []
    done = [w['event_id'] for w in warnings]
    calls, failed = 0, None
    for g in groups:
        try:
            started = time.monotonic()
            reply = client.complete(SYSTEM, build_prompt(g, home), max_tokens=max_tokens_for(g))
        except LLMError as e:
            failed = str(e)
            log.error('요약 실패 (%s): %s', (g.head.get('target') or {}).get('law_name'), e)
            break
        calls += 1
        tm, us = reply.timings, reply.usage
        log.info(
            '요약 %s: %.1fs prompt=%s gen=%s peak=%sGB finish=%s',
            (g.head.get('target') or {}).get('law_name'),
            time.monotonic() - started,
            us.get('prompt_tokens'),
            us.get('completion_tokens'),
            tm.get('peak_memory_gb'),
            reply.finish_reason,
        )
        rendered.append((_section(g), _render(g, reply.text, paths, truncated=reply.finish_reason == 'length')))
        done += [e['event_id'] for e in g.events]

    pending = len(events) - len(done)
    counts = {
        '법령': sum(e.get('source') == 'legalize_law' for e in events),
        '행정규칙': sum(e.get('source') == 'legalize_admrule' for e in events),
        '별표서식': sum(e.get('source') in ('lawgo_forms', 'lawgo_admrul') for e in events),
    }
    md = [
        f'# 법령 변경 요약 {title_date}',
        '',
        f'> 자동 요약입니다 (모델 `{client.model}`). 시행일·금액·서식 내용은 반드시 아래 원문으로 확인하세요.',
        '',
        f'- 실행: {ran_at}',
        *([f'- 대상: {window}'] if window else []),
        f'- 수집: {run_line or "(이번에는 summarize만 실행)"}',
        f'- 이벤트 {len(events)}건 ({", ".join(f"{k} {v}" for k, v in counts.items())}) · 모델 호출 {calls}번',
        '',
    ]
    if not events:
        md += ['## 새 변경 없음', '']
    current = None
    for section, text in rendered:
        if section != current:
            md += [f'## {section}. {CATEGORY_TITLES.get(section, "기타")}', '']
            current = section
        md += [text]
    if failed:
        md += ['## 요약 실패', '', f'- {failed}', f'- 대기 이벤트 {pending}건: 다음 실행에서 다시 요약합니다', '']
    if warnings:
        md += ['## 경고', '']
        for w in warnings:
            c = w.get('context') or {}
            md.append(f'- `{w.get("source")}` 수집기 {c.get("consecutive_failures")}회 연속 실패: {c.get("error")}')
        md.append('')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(md).rstrip('\n') + '\n', encoding='utf-8')
    log.info('summarize events=%d calls=%d pending=%d → %s', len(events), calls, pending, out_path.name)
    return SummaryResult(out_path, len(events), calls, failed, pending), done


def load_events(home: Path, items: list[tuple[str, str | None]]) -> tuple[list[dict], dict[str, str]]:
    events, paths = [], {}
    for event_id, rel in items:
        path = home / rel if rel else None
        if path is None or not path.exists():
            log.warning('이벤트 파일 없음: %s (%s)', event_id, rel)
            continue
        events.append(json.loads(path.read_text(encoding='utf-8')))
        paths[event_id] = rel
    return events, paths
