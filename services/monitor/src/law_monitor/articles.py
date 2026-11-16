"""본문을 조문(##### 제N조)과 부칙 블록(부칙 <제N호,날짜>) 단위로 나눠 바뀐 부분만 diff한다.

전부개정처럼 diff가 큰 이벤트를 2단계(요약 모델)가 조각으로 읽을 수 있게 하기 위한 것 (원 계획서 9장 diff.chunks).
"""

import difflib
import re

ARTICLE = re.compile(r'^#{5}\s*(제\d+조(?:의\d+)*)')
ADDENDA_HEADING = re.compile(r'^##\s*부칙\s*$')
ADDENDUM_BLOCK = re.compile(r'^부칙\s*<[^>]*>')
HEAD = '머리'  # 첫 조문 앞 (제목·편장절 제목)


def split_chunks(body: str) -> dict[str, str]:
    chunks: dict[str, list[str]] = {HEAD: []}
    key, in_addenda = HEAD, False
    for line in body.splitlines():
        new_key = None
        if not in_addenda and ADDENDA_HEADING.match(line):
            in_addenda, new_key = True, '부칙'
        elif in_addenda and ADDENDUM_BLOCK.match(line):
            new_key = line.strip()
        elif not in_addenda and (m := ARTICLE.match(line)):
            new_key = m.group(1)
        if new_key is not None:
            key = new_key
            n = 2
            while key in chunks:  # 같은 이름이 또 나오면 번호를 붙임
                key, n = f'{new_key}#{n}', n + 1
            chunks[key] = []
        chunks[key].append(line)
    return {k: '\n'.join(v).strip('\n') for k, v in chunks.items()}


def _diff(old: str, new: str) -> str:
    return '\n'.join(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=2))


def chunk_diffs(old_body: str, new_body: str) -> list[dict]:
    old, new = split_chunks(old_body), split_chunks(new_body)
    out = []
    for key, text in new.items():
        if key not in old:
            out.append({'article': key, 'kind': 'added', 'diff': _diff('', text)})
        elif old[key] != text:
            out.append({'article': key, 'kind': 'changed', 'diff': _diff(old[key], text)})
    out += [{'article': k, 'kind': 'removed', 'diff': _diff(t, '')} for k, t in old.items() if k not in new]
    return [c for c in out if c['diff'] or c['kind'] != 'changed']


def added_addenda(old_body: str, new_body: str) -> list[str]:
    """새로 생긴 부칙 블록 원문 (시행일·경과조치가 들어 있음)."""
    old = split_chunks(old_body)
    return [text for key, text in split_chunks(new_body).items() if ADDENDUM_BLOCK.match(key) and key not in old]
