"""Legalize KR 원문과 기준일로 조문 CSV 두 개를 만든다 (계획: docs/archives/article-generator.md)

    git clone --filter=blob:none --no-checkout https://github.com/legalize-kr/legalize-kr ../legalize-kr
    uv run lawbot-build-articles --repo ../legalize-kr --asof 2026-10-20 --out-dir data/v4 [--check]

출력 (UTF-8 BOM, 행 끝 LF)
- articles_v4.csv: 7개 법률
- law_articles_v4_decree.csv: 법률 + 시행령·시행규칙 (행 순서는 doc_order)
- --check: 파일을 쓰지 않고 out-dir의 기존 파일과 바이트 단위로 비교 (다르면 종료 코드 1)

규칙 (법률·시행령·시행규칙 21개 문서 모두 같다)
- 파일: kr/<법령명 띄어쓰기 없이>/ 안에서 기준일 버전의 머리말 `제목`이 문서명이고 폐지 커밋이 아닌 파일
- 조합: 파일 이력(커밋 날짜 = 공포일)에서 공포일 ≤ 기준일인 버전을 오래된 것부터 보며, 직전 버전과 달라진 조문
  (신설·삭제 포함)과 장 제목에 그 개정의 시행일을 붙이고 시행일 ≤ 기준일인 변경만 반영한다. 시행일은 머리말
  `시행일자`, 부칙 제1조 '다만'에 적힌 조문·장은 그 날짜 (조문 일부만 적혀 있어도 조문 전체, 규모별이면 가장 이른 날)
- 원문 형식 변경(공백, 조문 끝 `[시행일]` 안내 삭제)은 개정으로 보지 않는다
- effective_date: 그 조문을 마지막으로 바꾼 개정의 시행일
- 유효기간: 부칙 '○의 개정규정은 …까지 효력을 가진다'가 기준일 전에 끝났으면 조문 전체는 deleted=1,
  항이면 그 항 끝에 `[실효: YYYY.M.D.까지 유효]`
- 본문: 굵게 표시·`\\.` 이스케이프·들여쓰기·빈 줄 제거. 별표·서식은 링크만 있어 넣지 않음
- 삭제: 제목이 없고 본문이 비었거나 '삭제'로 시작하면 deleted=1
- chapter: 장(과 절) 제목, 개정 표시 제외
- 연결: 본문의 조문 참조. 같은 문서 → related_ids(자기 자신 제외), 상위 문서('법 제N조', 시행규칙은 '영 제N조' 포함과
  그 뒤 접속어로만 이어지는 '제N조' 나열) → parent_ids, child_ids는 그 역방향. 없는 조문·삭제 조문으로는 잇지 않는다.
  '부칙 제N조'와 다른 법령 조문은 넣지 않는다
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

LAWS = [  # 대상 법률 (CSV의 법령 순서)
    '근로기준법',
    '근로자퇴직급여 보장법',
    '최저임금법',
    '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률',
    '기간제 및 단시간근로자 보호 등에 관한 법률',
    '파견근로자 보호 등에 관한 법률',
    '임금채권보장법',
]
KINDS = ['법률', '시행령', '시행규칙']
LAW_COLS = ['law', 'article_id', 'article_no', 'title', 'chapter', 'deleted', 'text', 'effective_date', 'related_ids']
DECREE_COLS = [*LAW_COLS, 'kind', 'parent_law', 'parent_ids', 'child_ids']
LAW_CSV, DECREE_CSV = 'articles_v4.csv', 'law_articles_v4_decree.csv'

ART = r'제(\d+)조(?:의(\d+))?'
HEAD = re.compile(r'^##### 제(\d+)조(?:의(\d+))?(?:\s*\((.*)\))?\s*$')
SECTION = re.compile(r'^(#{2,4}) (제\d+(?:장|절)(?:의\d+)?.*?)\s*(?:<(?:개정|신설)[^<>]*>)?\s*$')


# ── git ──────────────────────────────────────────────────────────────


class Repo:
    """legalize-kr 저장소 읽기. 파일 이력은 git log 한 번으로, 본문은 cat-file --batch로 (파일·버전마다 부르면 느림)"""

    def __init__(self, path):
        self.path = Path(path)
        self.history: dict[str, list[tuple[str, str, str]]] = {}  # 경로 → [(sha, 공포일, 제목)] 최신순
        self._blobs: dict[tuple[str, str], str] = {}

    def git(self, *args):
        return subprocess.run(
            ['git', '-C', str(self.path), '-c', 'core.quotepath=off', *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def head(self):
        return self.git('rev-parse', 'HEAD').strip()

    def load_history(self, paths):
        out = self.git('log', '--format=@%H %ad %s', '--date=short', '--name-only', 'HEAD', '--', *paths)
        cur = None
        for line in out.splitlines():
            if line.startswith('@'):
                sha, date, subject = line[1:].split(' ', 2)
                cur = (sha, date, subject)
            elif line.strip() and cur:
                self.history.setdefault(line.strip(), []).append(cur)

    def show(self, sha, path):
        return self.show_many([(sha, path)])[0]

    def show_many(self, items):
        missing = [i for i in dict.fromkeys(items) if i not in self._blobs]
        if missing:
            req = ''.join(f'{sha}:{path}\n' for sha, path in missing).encode()
            out = subprocess.run(
                ['git', '-C', str(self.path), 'cat-file', '--batch'], input=req, check=True, capture_output=True
            ).stdout
            pos = 0
            for key in missing:
                header_end = out.index(b'\n', pos)
                header = out[pos:header_end].split()
                if header[-1] == b'missing':
                    raise FileNotFoundError(f'{key[0][:10]}:{key[1]}')
                size = int(header[2])
                self._blobs[key] = out[header_end + 1 : header_end + 1 + size].decode('utf-8')
                pos = header_end + 1 + size + 1
        return [self._blobs[i] for i in items]


# ── 원문 파싱 ────────────────────────────────────────────────────────


def front(md):
    m = re.match(r'---\n(.*?)\n---\n', md, re.S)
    if not m:
        raise ValueError('머리말(YAML)이 없습니다')
    out = {}
    for line in m.group(1).splitlines():
        if line[:1] in (' ', '-'):  # 첨부파일 등 하위 항목
            continue
        k, _, v = line.partition(':')
        if v.strip():
            out[k.strip()] = v.strip().strip("'")
    return out


def clean(lines):
    out = []
    for s in lines:
        s = s.replace('**', '').replace('\\.', '.').strip()
        if s:
            out.append(s)
    return '\n'.join(out)


def split_addenda(md):
    body = md.split('\n---\n', 1)[1]
    parts = re.split(r'^## 부칙', body, maxsplit=1, flags=re.M)
    return parts[0], (parts[1] if len(parts) > 1 else '')


def parse(md):
    """본문 조문 목록 [{no, title, text, chapter}]. chapter: 장(과 절) 제목, 개정 표시 제외"""
    body, _ = split_addenda(md)
    arts, cur, chapter, section = [], None, None, None
    for line in body.splitlines():
        m = HEAD.match(line)
        if m:
            cur = {
                'no': f'제{m[1]}조' + (f'의{m[2]}' if m[2] else ''),
                'title': (m[3] or '').strip().strip('()').strip() or None,
                'lines': [],
                'chapter': ' '.join(x for x in (chapter, section) if x) or None,
            }
            arts.append(cur)
            continue
        s = SECTION.match(line)
        if s:
            if '장' in s[2].split()[0]:
                chapter, section = s[2], None
            else:
                section = s[2]
            cur = None
        elif cur is not None and not line.startswith('#'):
            cur['lines'].append(line)
    for a in arts:
        a['text'] = clean(a.pop('lines')) or None
    return arts


# ── 부칙 시행일 ──────────────────────────────────────────────────────


def months_later(start: dt.date, months: int) -> dt.date:
    """start(기산일)의 months개월 뒤 같은 날. 그런 날이 없으면 그다음 달 1일 (민법 제160조: 그 달 말일에 기간 만료)"""
    y, m = divmod(start.month - 1 + months, 12)
    y, m = start.year + y, m + 1
    if start.day <= calendar.monthrange(y, m)[1]:
        return dt.date(y, m, start.day)
    return dt.date(y + (m == 12), m % 12 + 1, 1)


DATE_EXPR = re.compile(
    r'공포한\s*날|공포\s*후\s*(\d+)\s*(개월|년)이\s*경과한\s*날|(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일'
)


def date_expr(m: re.Match, promulgated: str) -> str:
    """부칙의 날짜 표현 → YYYY-MM-DD. '공포 후 N개월이 경과한 날': 공포 다음 날부터 세어(초일 불산입) N개월 뒤
    (2026-04-07 + 8개월 → 2026-12-08, 2019-04-30 + 6개월 → 2019-11-01)"""
    p = dt.date.fromisoformat(promulgated)
    if m[1]:
        months = int(m[1]) * (12 if m[2] == '년' else 1)
        return months_later(p + dt.timedelta(days=1), months).isoformat()
    if m[3]:
        return dt.date(int(m[3]), int(m[4]), int(m[5])).isoformat()
    return p.isoformat()


def own_addendum(md, number):
    """이 버전의 부칙 본문 (`부칙… <제{공포번호}호,…>` 머리 아래). 없으면 None (부칙 없는 타법개정 등)"""
    _, addenda = split_addenda(md)
    number = str(int(number))
    heads = list(re.finditer(r'^부칙[^\n<]*<제(\d+)호,[^>]*>', addenda, re.M))
    for i, h in enumerate(heads):
        if str(int(h[1])) == number:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(addenda)
            return addenda[h.end() : end]
    return None


@dataclass
class Schedule:
    """한 개정의 시행일: main(머리말 시행일자) + 부칙 '다만'에 적힌 조문·장별 날짜"""

    main: str
    articles: dict[str, str] = field(default_factory=dict)  # '제13조' → 날짜
    chapters: dict[str, str] = field(default_factory=dict)  # '제11장' → 날짜

    def article(self, no):
        return self.articles.get(no, self.main)

    def chapter(self, title):
        key = (title or '').split(' ')[0]
        return self.chapters.get(key, self.main)


AFTER_ADDENDUM = re.compile(r'부칙\s*$')


def target_list(text, order):
    """'제13조, 제11장의 제목, 제103조부터 제105조까지, 제110조제1호 및 …' → (조문 목록, 장 목록)"""
    arts, chapters = [], []
    for m in re.finditer(rf'{ART}(?:제\d+항|제\d+호|제\d+목|[ㆍ·]제?\d+(?:항|호))*(\s*부터\s*{ART}\s*까지)?', text):
        if AFTER_ADDENDUM.search(text[: m.start()]):  # '부칙 제16조제21항': 부칙의 조문이라 본문 조문이 아님
            continue
        a = f'제{m[1]}조' + (f'의{m[2]}' if m[2] else '')
        if m[3]:
            b = f'제{m[4]}조' + (f'의{m[5]}' if m[5] else '')
            if a not in order or b not in order:
                raise ValueError(f'부칙 범위의 조문이 본문에 없습니다: {a}~{b}')
            arts.extend(order[order.index(a) : order.index(b) + 1])
        else:
            arts.append(a)
    for m in re.finditer(r'(?<![\d조])(제\d+장(?:의\d+)?)', text):
        chapters.append(m[1])
    return arts, chapters


def schedule(md, fm, order, asof=None) -> Schedule:
    """이 버전(개정)의 시행일. 부칙 제1조 '다만, ○의 개정규정은 ○부터 시행하고, …'를 읽는다
    asof: 주면 부칙 제1조에 기준일보다 늦은 날짜가 있는데 어느 조문의 것인지 못 읽었을 때 실패 ('②'로 나눈 형식 등)"""
    sched = Schedule(main=fm['시행일자'])
    text = own_addendum(md, fm['공포번호'])
    if not text:
        return sched
    m = re.search(r'제1조\s*\(시행일\)\s*(.*?)(?:\n\s*\n\s*제2조|\Z)', text, re.S)
    first = (m[1] if m else text).replace('\n', ' ')
    if '다만' in first:
        read_proviso(sched, first.split('다만', 1)[1], fm, order)
    if asof:
        known = {sched.main, *sched.articles.values(), *sched.chapters.values()}
        late = {date_expr(d, fm['공포일자']) for d in DATE_EXPR.finditer(first)} - known
        if any(d > asof for d in late):
            raise ValueError(
                f'제{fm["공포번호"]}호 부칙 제1조의 시행일 {sorted(late)}이 어느 조문의 것인지 읽지 못했습니다: {first[:200]}'
            )
    return sched


def read_proviso(sched: Schedule, proviso, fm, order):
    for clause in re.split(r'시행하고,|시행하며,', proviso):
        found = list(DATE_EXPR.finditer(clause))
        if '개정규정' not in clause or not found:
            continue
        arts, chapters = target_list(clause.split('개정규정', 1)[0], order)
        if not arts and not chapters:  # 별표 등 본문 조문이 아닌 것
            continue
        if '대통령령' in clause:
            raise ValueError(f'부칙 시행일을 날짜로 정할 수 없습니다: {clause.strip()[:200]}')
        date = min(date_expr(d, fm['공포일자']) for d in found)  # 사업장 규모별로 나뉘면 가장 이른 날
        for a in arts:
            sched.articles[a] = max(sched.articles.get(a, ''), date)
        for c in chapters:
            sched.chapters[c] = max(sched.chapters.get(c, ''), date)


NOTE_ITEM = re.compile(r'^(?:\d+\.|[가-하]\.)\s')


def strip_notes(text):
    """조문 끝 `[시행일] …` 안내 문단(규모별 시행일 등)과 그 아래 번호 줄을 뺀다. 원문이 나중에 지우기도 하는 안내라 개정이 아님"""
    out, skipping = [], False
    for line in (text or '').split('\n'):
        if line.startswith('[시행일]'):
            skipping = True
            continue
        if skipping and NOTE_ITEM.match(line):
            continue
        skipping = False
        out.append(line)
    return '\n'.join(out)


def squash(s):
    """공백과 `[시행일]` 안내를 뺀 본문: 이것이 같으면 개정이 아니라 원문 형식 변경으로 본다"""
    return re.sub(r'\s+', '', strip_notes(s))


def in_force(repo: Repo, path, asof):
    """기준일에 시행 중인 조문 [{no, title, text, chapter, eff}]과 버전별 반영 기록

    eff: 그 조문(제목·본문)을 마지막으로 바꾼 개정의 시행일. 공백만 바뀐 변경(원문 형식 정리)은 개정으로 보지 않는다.
    시행 전 개정이 남은 조문에 그 뒤 시행된 개정이 내용을 바꾸면 조문 단위로 나눌 수 없어 실패한다"""
    hist = [h for h in reversed(repo.history[path]) if h[1] <= asof]  # 오래된 것부터
    mds = repo.show_many([(sha, path) for sha, _, _ in hist])
    state: dict[str, dict] = {}
    prev: dict[str, dict] = {}
    pending: set[str] = set()
    expiries: list[tuple[str, list[int], str]] = []
    used = []
    order: list[str] = []
    for (sha, _, _), md in zip(hist, mds):
        fm = front(md)
        arts = {a['no']: a for a in parse(md)}
        cur_order = list(arts)
        sched = schedule(md, fm, cur_order, asof)
        expiries += expiry_clauses(own_addendum(md, fm['공포번호']) or '')
        applied = waiting = 0
        for no in dict.fromkeys(list(prev) + cur_order):
            old, new = prev.get(no), arts.get(no)
            if old == new:
                continue
            if new is None:  # 조문이 파일에서 빠짐
                if sched.article(no) > asof:
                    raise ValueError(f'{path} {no}: 시행 전에 조문이 빠지는 경우는 처리하지 않습니다')
                state.pop(no, None)
                continue
            cur = state.setdefault(no, {'no': no, 'title': None, 'text': None, 'chapter': None, 'eff': None})
            if old is None or (old['title'], old['text']) != (new['title'], new['text']):
                real = old is None or (old['title'], squash(old['text'])) != (new['title'], squash(new['text']))
                date = sched.article(no)
                if not real:  # 형식만 바뀜: 시행 전 개정이 없을 때만 따라감
                    if no not in pending:
                        cur.update(title=new['title'], text=new['text'])
                elif date <= asof:
                    if no in pending:
                        raise ValueError(
                            f'{path} {no}: 시행 전 개정이 남은 조문을 제{fm.get("공포번호")}호가 바꿉니다 '
                            '(조문 단위로 조합할 수 없음)'
                        )
                    cur.update(title=new['title'], text=new['text'], eff=date)
                    applied += 1
                else:
                    pending.add(no)
                    waiting += 1
            if old is None or old['chapter'] != new['chapter']:
                if sched.chapter(new['chapter']) <= asof:
                    cur['chapter'] = new['chapter']
            if cur['eff'] is None:  # 시행 전 신설 조문
                state.pop(no)
        prev = arts
        order = cur_order
        used.append(
            {
                'commit': sha[:10],
                '공포번호': fm.get('공포번호'),
                '시행일자': fm.get('시행일자'),
                '반영': applied,
                '시행 전': waiting,
            }
        )
    known = [no for no in order if no in state] + [no for no in state if no not in order]
    arts = [state[no] for no in known]
    expire(arts, expiries, asof, path)
    return arts, used


EXPIRY = re.compile(r'(.*?)의\s*개정규정은\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일까지\s*효력을\s*가진다')
PARA = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'


def expiry_clauses(addendum):
    """부칙 '제N조(유효기간…) ○의 개정규정은 YYYY년 M월 D일까지 효력을 가진다' → [(조문, [항], 날짜)]. 항이 없으면 조문 전체"""
    out = []
    for m in re.finditer(r'제\d+조\s*\(유효기간[^)]*\)\s*([^\n]*)', addendum):
        e = EXPIRY.search(m[1])
        if not e:
            continue
        date = dt.date(int(e[2]), int(e[3]), int(e[4])).isoformat()
        cur = None
        for t in re.finditer(r'제(\d+)(조(?:의(\d+))?|항)', e[1]):
            if t[2].startswith('조'):
                cur = (f'제{t[1]}조' + (f'의{t[3]}' if t[3] else ''), [])
                out.append((*cur, date))
            elif cur is not None:
                cur[1].append(int(t[1]))
    return out


def expire(arts, expiries, asof, path):
    """유효기간이 기준일 전에 끝난 개정규정: 조문 전체면 삭제 조문으로(deleted), 항이면 그 항 끝에 '[실효: …까지 유효]'"""
    by_no = {a['no']: a for a in arts}
    for no, paras, date in expiries:
        if date >= asof or no not in by_no:
            continue
        a = by_no[no]
        if not paras:
            a['expired'] = True
            continue
        y, m, d = (int(x) for x in date.split('-'))
        mark = f' [실효: {y}.{m}.{d}.까지 유효]'
        lines = a['text'].split('\n')
        for k in paras:
            starts = [i for i, line in enumerate(lines) if line.startswith(PARA[k - 1])]
            if len(starts) != 1:
                raise ValueError(f'{path} {no}: 유효기간이 끝난 제{k}항을 본문에서 찾지 못했습니다')
            nxt = [i for i, line in enumerate(lines) if i > starts[0] and line[:1] in PARA]
            last = (nxt[0] if nxt else len(lines)) - 1
            lines[last] += mark
        a['text'] = '\n'.join(lines)


# ── 조문 참조 (사용자 스크립트 그대로) ─────────────────────────────

# 참조 뒤에 이어지는 접속 표현(이 안에서는 앞 참조의 법령을 이어받음)
GLUE = re.compile(
    r'^(?:\([^()]*\)|\s|[,ㆍ·]|및|또는|이나|부터|까지|의|규정|제\d+항|제\d+호|제\d+목|'
    r'같은\s*조|같은\s*항|단서|본문|전단|후단|각\s*호|외의|부분|[가-하]목|\(|\))*$'
)
ANCHOR = re.compile(
    r'(「([^」]+)」|같은\s*법(?:\s*(시행령|시행규칙))?|(?<![가-힣])이\s*(?:법|영|규칙)|(?<![가-힣])영|(?<![가-힣])법)'
    r'\s*(?:\(이하[^()]*\))?\s*$'
)
# 앵커가 없어도 바로 앞 낱말이 다른 법령 이름(약칭)이면 외부 법령으로 본다. 예: '고용산재보험료징수법 시행령 제10조'
OTHER_NAME = re.compile(r'(?<![가-힣])[가-힣]+(?:법|령|규칙)(?:\s+(?:시행령|시행규칙))?\s*$')
SUB = re.compile(r'(?:제\d+항|제\d+호|제\d+목)+')


def refs(text, own, upper, names):
    """본문의 조문 참조를 (문서명, 조문번호, 범위시작) 목록으로.

    own=이 문서, upper={'법':..,'영':..}. '제A조부터 제B조까지'이면 B의 범위시작=A.
    """
    out, prev, prev_end, last_named = [], None, None, None
    for m in re.finditer(ART, text or ''):
        no = f'제{m[1]}조' + (f'의{m[2]}' if m[2] else '')
        before = text[max(0, m.start() - 80) : m.start()]
        a = ANCHOR.search(before)
        doc = None
        if a:
            tok = a[1]
            if a[2]:
                key = re.sub(r'\s+', '', a[2])
                doc = names.get(key, '__external__')
                last_named = doc
            elif tok.startswith('같은'):
                base = last_named or '__external__'
                doc = (
                    base
                    if not a[3] or base == '__external__'
                    else names.get(re.sub(r'\s+', '', f'{base}{a[3]}'), '__external__')
                )
            elif re.fullmatch(r'이\s*(법|영|규칙)', tok):
                doc = own
            elif tok in upper:
                doc = upper[tok]
            else:
                doc = '__external__'  # 법률 본문의 '법 제N조' 등 예상 밖 앵커
        gap = text[prev_end : m.start()] if prev_end is not None else None
        if doc is None and AFTER_ADDENDUM.search(before):  # '…법 부칙 제2조제1항': 다른 법률 부칙의 조문
            doc = '__external__'
        if doc is None:
            if gap is not None and prev and GLUE.match(gap):
                doc = prev[0]
            elif OTHER_NAME.search(before):
                doc = '__external__'
            else:
                doc = own
        start = prev[1] if (prev and prev[0] == doc and gap is not None and re.fullmatch(r'\s*부터\s*', gap)) else None
        out.append((doc, no, start))
        prev, prev_end = (doc, no), m.end()
        sub = SUB.match(text, m.end())
        if sub:
            prev_end = sub.end()
    return out


def expand(doc_refs, order):
    """(문서, 조문, 범위시작) → 조문 ID 목록. 범위는 문서 내 조문 순서로 펼친다."""
    ids = []
    for doc, no, start in doc_refs:
        aid = f'{doc} {no}'
        if start and doc in order and f'{doc} {start}' in order[doc] and aid in order[doc]:
            seq = order[doc]
            i, j = seq.index(f'{doc} {start}'), seq.index(aid)
            ids.extend(seq[i + 1 : j + 1])
        else:
            ids.append(aid)
    return ids


def join(ids):
    ids = list(dict.fromkeys(ids))
    return '|'.join(ids) if ids else None


def doc_name(law, kind):
    return law if kind == '법률' else f'{law} {kind}'


def doc_names():
    """공백 없앤 이름 → 문서명 (「…」 참조를 문서로)"""
    return {re.sub(r'\s+', '', doc_name(law, k)): doc_name(law, k) for law in LAWS for k in KINDS}


def find_files(repo: Repo, asof):
    """문서명 → 파일 경로. 법령 폴더에서 기준일 버전의 머리말 제목이 문서명이고, 그 버전이 폐지 커밋이 아닌 파일
    (예: 근로기준법 폴더의 법률.md는 1997년 '(폐지)' 커밋으로 끝난 옛 법, 법률(법률).md가 현행. 머리말 상태는 둘 다 '시행')"""
    paths = {}
    for law in LAWS:
        folder = f'kr/{law.replace(" ", "")}/'
        files = [f for f in repo.git('ls-tree', '--name-only', 'HEAD', folder).splitlines() if f.endswith('.md')]
        repo.load_history(files)
        for kind in KINDS:
            doc, found = doc_name(law, kind), []
            for f in files:
                hist = [h for h in repo.history.get(f, []) if h[1] <= asof]
                if not hist or hist[0][2].endswith('(폐지)'):
                    continue
                fm = front(repo.show(hist[0][0], f))
                if fm.get('제목', '').replace(' ', '') == doc.replace(' ', ''):
                    found.append(f)
            if len(found) != 1:
                raise ValueError(f'{doc}: 시행 중인 파일이 하나가 아닙니다 ({found or "없음"}, 폴더 {folder})')
            paths[doc] = found[0]
    return paths


# ── 조문 행 ──────────────────────────────────────────────────────────


def doc_order():
    """[(법령, 종류)] 행 순서: 법률은 LAWS 순서, 그 뒤 하위법령은 법령 이름 가나다순으로 시행령 → 시행규칙.
    순서를 바꾸지 않는 이유: 조문 임베딩(같은 batch에 든 문서에 따라 fp16 값이 달라짐)과 저장된 실험 산출물
    (data/decree_exp, 후보 번호 = 행 순번)이 이 순서에 묶여 있다"""
    return [(law, '법률') for law in LAWS] + [(law, k) for law in sorted(LAWS) for k in KINDS if k != '법률']


def build_rows(repo: Repo, paths, asof):
    rows, versions = [], []
    for law, kind in doc_order():
        doc = doc_name(law, kind)
        arts, used = in_force(repo, paths[doc], asof)
        for a in arts:
            text = a['text']
            deleted = a.get('expired') or (a['title'] is None and (text is None or text.startswith('삭제')))
            rows.append(
                {
                    'law': doc,
                    'article_id': f'{doc} {a["no"]}',
                    'article_no': a['no'],
                    'title': a['title'],
                    'chapter': a['chapter'],
                    'deleted': int(bool(deleted)),
                    'text': text,
                    'effective_date': a['eff'],
                    'kind': kind,
                    'parent_law': law,
                }
            )
        versions += [{'doc': doc, 'kind': kind, 'path': paths[doc], **u} for u in used]
    if len({r['article_id'] for r in rows}) != len(rows):
        raise ValueError('조문 ID가 겹칩니다')
    return rows, versions


def link(rows):
    """related_ids·parent_ids·child_ids. 통계: 없는 조문 참조, 다른 법령 조문 참조(넣지 않음) 수"""
    names = doc_names()
    order: dict[str, list[str]] = {}
    for r in rows:
        order.setdefault(r['law'], []).append(r['article_id'])
    known = {r['article_id'] for r in rows}
    alive = {r['article_id'] for r in rows if not r['deleted']}
    dangling, cross = [], 0
    for r in rows:
        law = r['parent_law']
        upper = {'법률': {}, '시행령': {'법': law}, '시행규칙': {'법': law, '영': f'{law} 시행령'}}[r['kind']]
        same, up = [], []
        for aid in expand([x for x in refs(r['text'], r['law'], upper, names) if x[0] != '__external__'], order):
            doc = aid.rsplit(' ', 1)[0]
            if aid not in known:
                dangling.append((r['article_id'], aid))
            elif aid not in alive:
                continue
            elif doc == r['law']:
                if aid != r['article_id']:
                    same.append(aid)
            elif doc in upper.values():
                up.append(aid)
            else:
                cross += 1  # 다른 노동법(예: 근로기준법 시행령 → 「남녀고용평등법」)
        r['related_ids'], r['parent_ids'] = join(same), join(up)
    child: dict[str, list[str]] = {}
    for r in rows:
        if not r['deleted']:
            for p in (r['parent_ids'] or '').split('|'):
                if p:
                    child.setdefault(p, []).append(r['article_id'])
    for r in rows:
        r['child_ids'] = join(child.get(r['article_id'], []))
    return {'dangling': dangling, 'cross': cross}


# ── 실행 ─────────────────────────────────────────────────────────────


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator='\n')
    return ('\ufeff' + buf.getvalue()).encode('utf-8')


def build(repo_path, asof):
    """{파일 이름: 바이트}, 버전 목록, 통계. 쓰지 않는다"""
    repo = Repo(repo_path)
    paths = find_files(repo, asof)
    rows, versions = build_rows(repo, paths, asof)
    stats = link(rows)
    df = pd.DataFrame(rows, columns=DECREE_COLS)
    law = df[df['kind'] == '법률']
    files = {LAW_CSV: to_csv_bytes(law[LAW_COLS]), DECREE_CSV: to_csv_bytes(df)}
    return files, versions, {**stats, 'head': repo.head(), 'law_rows': len(law), 'decree_rows': len(df), 'paths': paths}


def first_diff(a: bytes, b: bytes):
    la, lb = a.split(b'\n'), b.split(b'\n')
    for i, (x, y) in enumerate(zip(la, lb), 1):
        if x != y:
            return i, x.decode('utf-8', 'replace')[:200], y.decode('utf-8', 'replace')[:200]
    return min(len(la), len(lb)) + 1, '(끝)', '(끝)'


def main(argv=None):
    p = argparse.ArgumentParser(description='Legalize KR → 조문 CSV (법률, 법률+하위법령)')
    p.add_argument('--repo', required=True, help='legalize-kr 저장소 (git clone --filter=blob:none)')
    p.add_argument('--asof', required=True, help='기준일 YYYY-MM-DD: 이 날 시행 중인 조문')
    p.add_argument('--out-dir', required=True, type=Path)
    p.add_argument('--check', action='store_true', help='쓰지 않고 out-dir의 기존 파일과 바이트 비교')
    a = p.parse_args(argv)
    dt.date.fromisoformat(a.asof)

    files, versions, stats = build(a.repo, a.asof)
    print(f'[원문] legalize-kr HEAD {stats["head"]} · 기준일 {a.asof}')
    print(f'[조문] 법률 {stats["law_rows"]}행 · 법률+하위법령 {stats["decree_rows"]}행')
    print(f'[연계] 없는 조문 참조 {len(stats["dangling"])}건 · 다른 법령 참조 {stats["cross"]}건 제외')
    last: dict[str, dict] = {}
    for v in versions:
        last[v['doc']] = v
        if v['시행 전']:
            print(
                f'[시행 전] {v["doc"]} {v["commit"]} 제{v["공포번호"]}호 (시행 {v["시행일자"]}) 조문 {v["시행 전"]}개 제외'
            )
    for doc, v in last.items():
        print(f'[버전] {doc} 마지막 {v["commit"]} 제{v["공포번호"]}호 · {stats["paths"][doc]}')

    if a.check:
        bad = 0
        for fname, data in files.items():
            old = (a.out_dir / fname).read_bytes() if (a.out_dir / fname).is_file() else b''
            if old == data:
                print(f'[일치] {fname} ({len(data):,} bytes)')
            else:
                bad += 1
                line, mine, theirs = first_diff(data, old)
                print(f'[다름] {fname} {line}번째 줄\n  생성: {mine}\n  기존: {theirs}')
        return 1 if bad else 0

    a.out_dir.mkdir(parents=True, exist_ok=True)
    for fname, data in files.items():
        (a.out_dir / fname).write_bytes(data)
        print(f'[저장] {a.out_dir / fname}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
