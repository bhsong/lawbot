"""Legalize KR 원문과 기준일로 조문 CSV 두 개를 만든다 (계획: docs/archives/article-generator.md, docs/plans/v5-tax-scope.md)

    git clone --bare https://github.com/legalize-kr/legalize-kr ../legalize-kr   # 옛 버전을 모두 읽으므로 전체 클론
    uv run lawbot-build-articles --repo ../legalize-kr --asof 2026-10-20 --out-dir data/v4 [--check] [--scope 목록.csv]

출력 (UTF-8 BOM, 행 끝 LF)
- articles_v4.csv: 7개 법률 (+ --scope 문서의 법률)
- law_articles_v4_decree.csv: 법률 + 시행령·시행규칙 (행 순서는 docs())
- --check: 파일을 쓰지 않고 out-dir의 기존 파일과 바이트 단위로 비교 (다르면 종료 코드 1)
- --scope: 범위 목록(law·kind·article_no)의 문서는 목록 조문과 그 조문을 대신하는 부칙 특례 조문만, 노동 문서 뒤에

규칙 (모든 문서에 같다)
- 파일: kr/<법령명 띄어쓰기 없이>/ 안에서 기준일 버전의 머리말 `제목`이 문서명이고 폐지 커밋이 아닌 파일
- 조합: 파일 이력(커밋 날짜 = 공포일)에서 공포일 ≤ 기준일인 버전을 오래된 것부터 보며, 직전 버전과 달라진 조문
  (신설·삭제 포함)과 장 제목에 그 개정의 시행일을 붙이고 시행일 ≤ 기준일인 변경만 반영한다. 시행일은 머리말
  `시행일자`, 부칙 제1조 '다만'에 적힌 조문·장은 그 날짜 (조문 일부만 적혀 있어도 조문 전체, 규모별이면 가장 이른 날)
- 시작: 기준일에 시행 중인 마지막 전부개정(제정) 버전부터. 그 앞 버전은 본문이 이어지지 않는다
- 타법개정 부칙('부칙(정부조직법) <…>')의 조문은 그 법의 조문이라 머리말 시행일자만 쓴다
- 시행 전 개정이 남은 조문을 그 뒤 개정이 다시 바꾸면 조문 단위로 나눌 수 없다: 최신 본문 끝에
  `[주의: YYYY.M.D. 시행 예정인 개정 내용이 함께 들어 있음]`
- 부칙 특례(범위 목록 문서만): 부칙 조문이 '제N조…에도 불구하고'로 범위 안 본칙을 대신하고, 기한('…까지')이
  기준일 이후이며, 그 부칙이 시행 중이면 `부칙<제N호>제M조` 행으로 넣는다 (chapter = 부칙 머리)
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
    r'공포\s*한\s*날|공포\s*후\s*(?=\d)(?:(\d+)\s*년\s*)?(?:(\d+)\s*개월)?이\s*경과한\s*날'
    r'|(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일'
)


def date_expr(m: re.Match, promulgated: str) -> str:
    """부칙의 날짜 표현 → YYYY-MM-DD. '공포 후 N년 M개월이 경과한 날': 공포 다음 날부터 세어(초일 불산입) 그만큼 뒤
    (2026-04-07 + 8개월 → 2026-12-08, 2019-04-30 + 6개월 → 2019-11-01)"""
    p = dt.date.fromisoformat(promulgated)
    if m[1] or m[2]:
        months = int(m[1] or 0) * 12 + int(m[2] or 0)
        return months_later(p + dt.timedelta(days=1), months).isoformat()
    if m[3]:
        return dt.date(int(m[3]), int(m[4]), int(m[5])).isoformat()
    return p.isoformat()


def own_addendum(md, number, with_owner=False):
    """이 버전의 부칙 본문 (`부칙… <제{공포번호}호,…>` 머리 아래). 없으면 None (부칙 없는 타법개정 등).
    with_owner: (본문, 머리의 법령 이름). '부칙(정부조직법) <…>'처럼 타법개정이면 그 법 이름, 이 법의 개정이면 None"""
    _, addenda = split_addenda(md)
    number = str(int(number))
    heads = list(re.finditer(r'^부칙\s*(?:\(([^)]*)\))?[^\n<]*<제(\d+)호,[^>]*>', addenda, re.M))
    for i, h in enumerate(heads):
        if str(int(h[2])) == number:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(addenda)
            text = addenda[h.end() : end]
            return (text, h[1]) if with_owner else text
    return (None, None) if with_owner else None


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
# '법률 제9911호 국세기본법 일부개정법률 제21조': 다른 개정법률(·개정령)의 조문이라 본문 조문이 아님
OTHER_AMENDMENT = re.compile(r'제\d+호\s+[^,]*?(?:개정|폐지)(?:법률|령)\s*$')
ANNEX = re.compile(r'별표|서식')
UNKNOWN = '9999-12-31'  # 부칙에 날짜가 없는 시행일 ('동법의 시행일', '대통령령으로 정하는 날')
# '…의 개정규정 중 "…"에 관한 부분', '… 중 제59조제2항의 개정규정과 관련한 부분': 대상 조문을 한정하는 말이라 대상이 아님
QUALIFIER = re.compile(r'\s*중\s+.*?(?:에\s*관한|[과와]\s*관련한|[과와]\s*관련된)\s*부분')
QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
# 시행일 조항을 나누는 곳: 단서, 연결 어미, 문장 끝, 항
BOUNDARY = re.compile(
    r'(?=다만\s*,)|(?<=시행하고,)|(?<=시행하며,)|(?<=시행하되,)|(?<=시행한다\.)|(?<=같다\.)|(?=[②-⑳])'
)


def target_list(text, order):
    """'제13조, 제11장의 제목, 제103조부터 제105조까지(내지), 제110조제1호 및 …' → ([(조문, 일부만인지)], 장 목록).
    일부: 조문 뒤에 항·호·목이 붙은 것 ('제110조제1호', '제116조제2항제1호ㆍ제4호')"""
    arts, chapters = [], []
    rng = rf'(\s*(?:부터|내지)\s*{ART}(?:\s*까지)?)?'
    for m in re.finditer(rf'{ART}((?:제\d+항|제\d+호|제\d+목|[ㆍ·]제?\d+(?:항|호))*){rng}', text):
        before = text[: m.start()]
        if AFTER_ADDENDUM.search(before) or OTHER_AMENDMENT.search(before):  # '부칙 제16조제21항' 등: 본문 조문이 아님
            continue
        a = f'제{m[1]}조' + (f'의{m[2]}' if m[2] else '')
        if m[4]:
            b = f'제{m[5]}조' + (f'의{m[6]}' if m[6] else '')
            if a not in order or b not in order:
                raise ValueError(f'부칙 범위의 조문이 본문에 없습니다: {a}~{b}')
            arts.extend((x, False) for x in order[order.index(a) : order.index(b) + 1])
        else:
            arts.append((a, bool(m[3])))
    for m in re.finditer(r'(?<![\d조])(제\d+장(?:의\d+)?)', text):
        chapters.append(m[1])
    return arts, chapters


def effective_clause(addendum):
    """부칙에서 시행일 조항만: '제1조(시행일)'이 있으면 그 조 전체(항 포함), 없으면 '①(시행일)' 항, 둘 다 없으면
    제2조·② 앞까지.
    적용례·경과조치(②(적용례) …, 제2조 …)의 '개정규정'은 시행일이 아니라 넣지 않는다"""
    m = re.search(r'제1조\s*\(시행일[^)]*\)\s*(.*?)(?=\n\s*\n\s*제2조|\Z)', addendum, re.S)
    if not m:
        m = re.search(r'①\s*\(시행일[^)]*\)\s*(.*?)(?=[②-⑳]|\Z)', addendum, re.S)
    if not m:  # 제목 없는 한 문단짜리 부칙: 제2조·②(다른 법령의 개정 등) 앞까지
        m = re.match(r'\s*(.*?)(?=\n\s*\n\s*(?:제2조|[②-⑳])|\Z)', addendum, re.S)
    text = m[1] if m else ''
    return re.sub(r'\s+', ' ', text).strip()


def clauses(first):
    """시행일 조항 → [[문장, 딸린 번호 목록]]. 번호 목록(1. … 2. …)은 앞에서 '다음 각 호'라고 한 문장의 것"""
    out: list[list[str]] = []
    owner = None
    for part in (x.strip() for x in BOUNDARY.split(first)):
        if not part:
            continue
        if re.match(r'\d+\.\s', part) and owner is not None:
            owner[1] += ' ' + part
            continue
        out.append([part, ''])
        if re.search(r'다음\s*각\s*호', part):
            owner = out[-1]
    return out


def each_item(items):
    """'1. … 2. …' → 번호를 뗀 호 목록"""
    return [re.sub(r'^\d+\.\s*', '', x) for x in re.split(r'(?:^|\s)(?=\d+\.\s)', items.strip()) if x.strip()]


def place(sched: Schedule, placed: set, sentence, items, fm, order, named=False):
    """'○의 개정규정은 ○부터 시행' 한 문장(과 딸린 번호 목록): 날짜 앞에서 대상 조문·장, 뒤에서 날짜(여럿이면 가장 이른 날).
    named: '다음 각 호의 개정규정은 해당 호에서 정하는 날부터'의 호라 '개정규정'이라는 말이 없어도 읽는다"""
    if not named and '개정규정' not in sentence:
        return
    d = DATE_EXPR.search(sentence)
    cut = min(i for i in (d.start() if d else -1, sentence.find('다음'), len(sentence)) if i >= 0)
    targets = QUOTED.sub('', QUALIFIER.sub('', sentence[:cut]))
    arts, chapters = target_list(targets, order)
    dates = [date_expr(x, fm['공포일자']) for x in DATE_EXPR.finditer(sentence[cut:] + ' ' + items)]
    if not arts and not chapters:
        # '다만, 다음 각 호의 개정규정은 해당 호에서 정하는 날부터': 호마다 대상과 날짜
        if items and re.search(r'다음\s*각\s*호', sentence):
            for item in each_item(items):
                place(sched, placed, item, '', fm, order, named='개정규정' in sentence)
        elif ANNEX.search(targets):  # 별표·서식만: 데이터에 없으므로 그 날짜는 읽은 것으로 친다
            placed.update(dates)
        return
    date = min(dates) if dates else UNKNOWN
    placed.update(dates)
    for a, part in arts:  # 조문 일부만이면 나머지는 본 시행일: 조문 전체가 시행되는 날
        sched.articles[a] = max(sched.articles.get(a, ''), max(date, sched.main) if part else date)
    for c in chapters:
        sched.chapters[c] = max(sched.chapters.get(c, ''), date)


def schedule(md, fm, order, asof=None) -> Schedule:
    """이 버전(개정)의 시행일: 머리말 `시행일자` + 부칙 시행일 조항에서 '○의 개정규정은 ○부터 시행'한다는 조문·장별 날짜.
    사업장 규모별(다음 각 호)이면 가장 이른 날. 조문 일부(항·호)만 적혔으면 그 날과 본 시행일 중 늦은 날,
    한 조문에 날짜가 여럿이면 가장 늦은 날 (조문 전체가 시행되는 날).
    asof: 주면 시행일 조항에 기준일 뒤 날짜가 있는데 어느 조문의 것인지 못 읽었을 때 실패"""
    sched = Schedule(main=fm['시행일자'])
    text, owner = own_addendum(md, fm['공포번호'], with_owner=True)
    if not text or owner:  # 타법개정 부칙의 조문은 그 법의 조문: 이 법에는 머리말 시행일자
        return sched
    first = effective_clause(text)
    placed: set[str] = set()  # 대상 조문을 읽은 문장의 날짜
    for sentence, items in clauses(first):
        if '개정규정' not in sentence:
            if '개정규정' not in items:
                continue
            # '다음 각 호의 부분은 각 호에서 정한 날부터': 호마다 '○의 개정규정 …: 날짜'
            for item in each_item(items):
                place(sched, placed, item, '', fm, order)
            continue
        place(sched, placed, sentence, items, fm, order)
    if asof:
        known = {sched.main, *placed}
        late = {date_expr(d, fm['공포일자']) for d in DATE_EXPR.finditer(first)} - known
        if any(d > asof for d in late):
            raise ValueError(
                f'제{fm["공포번호"]}호 부칙 시행일 {sorted(late)}이 어느 조문의 것인지 읽지 못했습니다: {first[:200]}'
            )
    return sched


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


FULL_REVISION = re.compile(r'\((?:전부개정|제정|폐지제정)\)$')


def last_full_revision(hist, mds, asof):
    """조합을 시작할 버전: 기준일에 시행 중인 마지막 전부개정(제정). 전부개정은 본문 전체를 새로 써서 앞 버전과 이어지지 않는다"""
    for i in range(len(hist) - 1, -1, -1):
        if FULL_REVISION.search(hist[i][2]) and front(mds[i])['시행일자'] <= asof:
            return i
    return 0


def in_force(repo: Repo, path, asof):
    """기준일에 시행 중인 조문 [{no, title, text, chapter, eff}]과 버전별 반영 기록

    eff: 그 조문(제목·본문)을 마지막으로 바꾼 개정의 시행일. 공백만 바뀐 변경(원문 형식 정리)은 개정으로 보지 않는다.
    시행 전 개정이 남은 조문에 그 뒤 시행된 개정이 내용을 바꾸면 조문 단위로 나눌 수 없다: 최신 본문을 쓰고 끝에
    시행 전 내용이 섞였다고 적는다(mixed, 2026-11-03 사용자 결정)"""
    hist = [h for h in reversed(repo.history[path]) if h[1] <= asof]  # 오래된 것부터
    mds = repo.show_many([(sha, path) for sha, _, _ in hist])
    start = last_full_revision(hist, mds, asof)
    hist, mds = hist[start:], mds[start:]
    state: dict[str, dict] = {}
    prev: dict[str, dict] = {}
    pending: dict[str, list[str]] = {}  # 시행 전 개정이 남은 조문 → 그 시행일들
    expiries: list[tuple[str, list[int], str]] = []
    used = []
    order: list[str] = []
    for (sha, _, _), md in zip(hist, mds):
        fm = front(md)
        arts = {a['no']: a for a in parse(md)}
        cur_order = list(arts)
        sched = schedule(md, fm, cur_order, asof)
        expiries += expiry_clauses(own_addendum(md, fm['공포번호']) or '')
        applied = 0
        waiting: list[tuple[str, str]] = []  # 시행 전이라 반영하지 않은 (조문, 시행일)
        mixed: list[tuple[str, list[str]]] = []  # 시행 전 개정이 남은 채로 다시 바뀐 (조문, 시행 전 날짜)
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
                elif date == UNKNOWN:  # 부칙에 날짜가 없음: 뒤 개정이 다시 바꾸지 않으면 끝에서 실패
                    cur.update(title=new['title'], text=new['text'], eff=sched.main, unknown=fm.get('공포번호'))
                    applied += 1
                elif date <= asof:
                    cur.pop('unknown', None)
                    if no in pending:
                        cur['mixed'] = sorted(set(pending[no]))
                        mixed.append((no, cur['mixed']))
                    cur.update(title=new['title'], text=new['text'], eff=date)
                    applied += 1
                else:
                    pending.setdefault(no, []).append(date)
                    waiting.append((no, date))
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
                '시행 전': len(waiting),
                'pending': waiting,
                'mixed': mixed,
            }
        )
    unknown = {no: a['unknown'] for no, a in state.items() if a.get('unknown')}
    if unknown:
        raise ValueError(f'{path}: 시행일을 부칙에서 날짜로 읽을 수 없는 개정이 지금 본문입니다 {unknown}')
    known = [no for no in order if no in state] + [no for no in state if no not in order]
    arts = [state[no] for no in known]
    expire(arts, expiries, asof, path)
    for a in arts:
        if a.get('mixed'):
            dates = ', '.join(dotted(d) for d in a['mixed'])
            a['text'] = f'{a["text"]}\n[주의: {dates} 시행 예정인 개정 내용이 함께 들어 있음]'
    return arts, used


ADDENDUM_HEAD = re.compile(r'^부칙\s*(?:\(([^)]*)\))?[^\n<]*<제(\d+)호,[^>]*>', re.M)
ADDENDUM_ART = re.compile(r'^제(\d+)조(?:의(\d+))?\s*\(([^)]*)\)', re.M)
OVERRIDE = re.compile(
    rf'((?:{ART}(?:제\d+항|제\d+호|제\d+목|[ㆍ·]제?\d+(?:항|호))*\s*(?:,|및|ㆍ|또는)?\s*)+)(?:의\s*개정규정)?\s*에도\s*불구하고'
)
# '「다른 법」 제3조', '법 제3조', '부칙 제2조': 이 문서 본칙이 아님
OTHER_DOC = re.compile(r'(?:」|법|영|규칙|부칙)\s*$')
UNTIL = re.compile(r'(\d{4})년(?:\s*(\d{1,2})월\s*(\d{1,2})일)?(?:분)?까지')


def addendum_overrides(md, used, asof, main_nos):
    """부칙 특례 조문 [{no, title, text, chapter, eff, targets}]: 부칙 조문이 '제N조…(의 개정규정)에도 불구하고'로 이 문서 본칙
    (main_nos 안)을 대신하고, 본문의 기한('…년까지', '…년 …월 …일까지') 중 가장 늦은 날이 기준일 이후이며, 그 부칙의
    개정이 기준일에 시행 중인 것. used: in_force의 버전 기록(조합한 버전의 부칙만 본다). 타법개정 부칙('부칙(정부조직법)')은
    그 법의 조문이라 넣지 않는다"""
    _, addenda = split_addenda(md)
    in_force_numbers = {str(int(u['공포번호'])) for u in used if u['시행일자'] <= asof}
    heads = list(ADDENDUM_HEAD.finditer(addenda))
    out = []
    for i, h in enumerate(heads):
        number = str(int(h[2]))
        if h[1] or number not in in_force_numbers:
            continue
        block = addenda[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(addenda)]
        arts = list(ADDENDUM_ART.finditer(block))
        for j, a in enumerate(arts):
            raw = block[a.end() : arts[j + 1].start() if j + 1 < len(arts) else len(block)]
            text = clean(raw.split('\n'))
            targets = [
                f'제{t[0]}조' + (f'의{t[1]}' if t[1] else '')
                for m in OVERRIDE.finditer(text)
                if not OTHER_DOC.search(text[: m.start()])
                for t in re.findall(ART, m[1])
            ]
            targets = [t for t in dict.fromkeys(targets) if t in main_nos]
            ends = [dt.date(int(y), int(mo or 12), int(d or 31)).isoformat() for y, mo, d in UNTIL.findall(text)]
            if not targets or not ends or max(ends) < asof:
                continue
            no = f'제{a[1]}조' + (f'의{a[2]}' if a[2] else '')
            eff = next(u['시행일자'] for u in used if str(int(u['공포번호'])) == number)
            out.append(
                {
                    'no': f'부칙<제{number}호>{no}',
                    'title': a[3].strip(),
                    'text': text,
                    'chapter': h[0].strip(),
                    'eff': eff,
                    'targets': targets,
                }
            )
    return out


def dotted(date):
    """'2027-01-01' → '2027.1.1.' (원문 날짜 표기)"""
    y, m, d = (int(x) for x in date.split('-'))
    return f'{y}.{m}.{d}.'


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
        mark = f' [실효: {dotted(date)}까지 유효]'
        lines = a['text'].split('\n')
        for k in paras:
            starts = [i for i, line in enumerate(lines) if line.startswith(PARA[k - 1])]
            if len(starts) != 1:
                raise ValueError(f'{path} {no}: 유효기간이 끝난 제{k}항을 본문에서 찾지 못했습니다')
            nxt = [i for i, line in enumerate(lines) if i > starts[0] and line[:1] in PARA]
            last = (nxt[0] if nxt else len(lines)) - 1
            lines[last] += mark
        a['text'] = '\n'.join(lines)


def commits_since(repo: Repo, ref, paths):
    """ref(매니페스트 source.ref) 뒤에 문서 파일을 바꾼 커밋 [{doc, sha, date(공포일), subject, 공포번호, 시행일자}] 오래된 것부터.
    ref가 없거나 클론에 없으면 None (커밋 날짜가 공포일이라 날짜로는 '그 뒤에 들어온 것'을 알 수 없다)"""
    if not ref:
        return None
    try:
        repo.git('cat-file', '-e', f'{ref}^{{commit}}')
    except subprocess.CalledProcessError:
        return None
    docs = {path: doc for doc, path in paths.items()}
    out = repo.git(
        'log', '--reverse', '--format=@%H %ad %s', '--date=short', '--name-only', f'{ref}..HEAD', '--', *docs
    )
    found, cur = [], None
    for line in out.splitlines():
        if line.startswith('@'):
            sha, date, subject = line[1:].split(' ', 2)
            cur = (sha, date, subject)
        elif line.strip() in docs and cur:
            fm = front(repo.show(cur[0], line.strip()))
            found.append(
                {
                    'doc': docs[line.strip()],
                    'sha': cur[0],
                    'date': cur[1],
                    'subject': cur[2],
                    '공포번호': str(int(fm.get('공포번호', '0'))),
                    '시행일자': fm.get('시행일자'),
                }
            )
    return found


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


def doc_names(doc_list=None):
    """공백 없앤 이름 → 문서명 (「…」 참조를 문서로)"""
    pairs = [(law, kind) for law, kind, _ in doc_list] if doc_list else [(law, k) for law in LAWS for k in KINDS]
    return {re.sub(r'\s+', '', doc_name(law, k)): doc_name(law, k) for law, k in pairs}


def find_files(repo: Repo, asof, doc_list=None):
    """문서명 → 파일 경로. 법령 폴더에서 기준일 버전의 머리말 제목이 문서명이고, 그 버전이 폐지 커밋이 아닌 파일
    (예: 근로기준법 폴더의 법률.md는 1997년 '(폐지)' 커밋으로 끝난 옛 법, 법률(법률).md가 현행. 머리말 상태는 둘 다 '시행')"""
    kinds_of: dict[str, list[str]] = {}
    for law, kind, _ in doc_list or docs(None):
        kinds_of.setdefault(law, []).append(kind)
    paths = {}
    for law, kinds in kinds_of.items():
        folder = f'kr/{law.replace(" ", "")}/'
        files = [f for f in repo.git('ls-tree', '--name-only', 'HEAD', folder).splitlines() if f.endswith('.md')]
        repo.load_history(files)
        for kind in kinds:
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


def load_scope(path):
    """범위 목록 CSV(law·kind·article_no 열) → {(법령, 종류): {조문번호}}. 조문 일부만 넣는 문서"""
    df = pd.read_csv(path, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    scope: dict[tuple[str, str], set[str]] = {}
    for law, kind, no in df[['law', 'kind', 'article_no']].itertuples(index=False):
        scope.setdefault((law, kind), set()).add(no)
    return scope


def docs(scope):
    """[(법령, 종류, 범위)] 만들 문서와 행 순서. 노동 7개 법률 문서는 전체(범위 None)로 doc_order() 그대로, 범위 목록 문서는
    그 뒤에 같은 규칙(법률은 목록에 나온 순서, 하위법령은 법령 이름순으로 시행령 → 시행규칙)"""
    out = [(law, kind, None) for law, kind in doc_order()]
    if not scope:
        return out
    laws = list(dict.fromkeys(law for law, _ in scope))
    order = [(law, '법률') for law in laws] + [(law, k) for law in sorted(laws) for k in KINDS if k != '법률']
    return out + [(law, kind, scope[(law, kind)]) for law, kind in order if (law, kind) in scope]


def build_rows(repo: Repo, paths, asof, doc_list=None):
    """조문 행과 버전 기록. 범위가 있는 문서는 범위 안 조문과 그 조문을 대신하는 부칙 특례 조문(addendum_overrides)만"""
    rows, versions = [], []
    for law, kind, scope in doc_list or docs(None):
        doc = doc_name(law, kind)
        arts, used = in_force(repo, paths[doc], asof)
        if scope is not None:
            missing = scope - {a['no'] for a in arts}
            if missing:
                raise ValueError(f'{doc}: 범위 목록의 조문이 없습니다 {sorted(missing)}')
            last = repo.show(next(h[0] for h in repo.history[paths[doc]] if h[1] <= asof), paths[doc])
            arts = [a for a in arts if a['no'] in scope] + addendum_overrides(last, used, asof, scope)
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


def link(rows, names=None):
    """related_ids·parent_ids·child_ids. 통계: 없는 조문 참조, 다른 법령 조문 참조(넣지 않음) 수"""
    names = names or doc_names()
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


def build(repo_path, asof, scope=None):
    """{파일 이름: 바이트}, 버전 목록, 통계. 쓰지 않는다. scope: load_scope() 결과 (조문 일부만 넣는 문서)"""
    repo = Repo(repo_path)
    doc_list = docs(scope)
    paths = find_files(repo, asof, doc_list)
    rows, versions = build_rows(repo, paths, asof, doc_list)
    stats = link(rows, doc_names(doc_list))
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
    p.add_argument('--repo', required=True, help='legalize-kr 저장소 (전체 클론: 옛 버전을 모두 읽음)')
    p.add_argument('--asof', required=True, help='기준일 YYYY-MM-DD: 이 날 시행 중인 조문')
    p.add_argument('--out-dir', required=True, type=Path)
    p.add_argument('--check', action='store_true', help='쓰지 않고 out-dir의 기존 파일과 바이트 비교')
    p.add_argument(
        '--scope', type=Path, help='범위 목록 CSV (law·kind·article_no): 이 문서들은 목록 조문만 노동 문서 뒤에'
    )
    a = p.parse_args(argv)
    dt.date.fromisoformat(a.asof)

    files, versions, stats = build(a.repo, a.asof, load_scope(a.scope) if a.scope else None)
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
        for no, dates in v['mixed']:
            print(
                f'[시행 전 포함] {v["doc"]} {no}: 제{v["공포번호"]}호가 시행 전({", ".join(dates)}) 개정이 남은 본문을 다시 바꿈'
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
