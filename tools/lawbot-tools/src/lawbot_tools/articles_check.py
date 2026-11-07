"""조문 데이터 점검: 오늘 시행 중인 법으로 만든 조문과 지금 데이터(data/v4)를 비교한다 (계획: docs/archives/monitor-article-check.md)

    uv run lawbot-articles-check --repo <legalize-kr 클론> [--asof YYYY-MM-DD] [--data-dir data/v4] [--report-dir DIR]

- 생성기(build_articles)로 기준일(기본 오늘, 한국 시간)에 시행 중인 조문을 메모리에서 만들고 법률+하위법령 CSV와 비교한다.
  파일은 쓰지 않는다 (보고서만). CSV 교체·재인덱싱·재시작은 사람이 한다 (본문이 바뀌면 임베딩·4B를 val로 다시 봐야 함)
- 보고서: 답변 근거가 다른 조문(추가·없어짐·본문·제목·삭제 여부), 그 밖 열 변화 개수, 시행 예정 개정,
  데이터를 만든 뒤(매니페스트 source.ref 이후) 들어온 개정, 다르면 다시 만드는 명령
- 마지막 줄: `articles same=yes|no changed=N pending=N next=날짜 new=N [→ 보고서]` (차후 law-monitor가 이 줄을 요약에 붙일 예정)
- 종료 코드: 0 같음, 1 답변 근거가 다른 조문 있음, 2 실행 실패
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from lawbot import deploy, envfile, manifest, paths
from lawbot_tools import build_articles as ba
from lawbot_tools.build_articles import commits_since

ANSWER_COLS = ('title', 'text', 'deleted')  # 런타임(검색·프롬프트·화면)이 쓰는 열 (law·article_id는 키)
DIFF_LINES = 8


@dataclass(frozen=True)
class Dataset:
    version: str = 'v4'

    @property
    def source_dir(self) -> Path:
        return paths.DATA / self.version

    @property
    def live_dir(self) -> Path:
        live = paths.DATA / 'live'
        return live / self.version if self.version == 'v5' else live

    @property
    def scope(self) -> Path | None:
        return self.source_dir / 'scope_tax.csv' if self.version == 'v5' else None

    @property
    def manifests(self) -> dict[str, str]:
        law, combined = ba.csv_names(self.version)
        decree_manifest = f'manifest_{Path(combined).stem}.json' if self.scope else 'manifest_decree.json'
        return {law: 'manifest.json', combined: decree_manifest}


V4 = Dataset()


def today() -> str:
    return dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()


@dataclass
class Diff:
    answer: list[dict] = field(default_factory=list)  # [{article_id, kind, effective_date, old, new}]
    meta: dict[str, int] = field(default_factory=dict)  # 그 밖 열 → 바뀐 조문 수


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype(object).where(df.notna(), '').astype(str).set_index('article_id', drop=False)


def compare(old: pd.DataFrame, new: pd.DataFrame) -> Diff:
    """조문 단위 비교. 답변 근거 변화는 어느 한쪽에서라도 살아 있는(deleted=0) 조문만"""
    old, new = _norm(old), _norm(new)
    diff = Diff()
    others = [c for c in new.columns if c in old.columns and c not in (*ANSWER_COLS, 'law', 'article_id')]
    for aid in list(old.index) + [i for i in new.index if i not in old.index]:
        o = old.loc[aid] if aid in old.index else None
        n = new.loc[aid] if aid in new.index else None
        if o is None or n is None:
            kind, alive = ('추가', n['deleted'] == '0') if o is None else ('없어짐', o['deleted'] == '0')
            parts = [kind] if alive else []
        else:
            parts = []
            if o['deleted'] != n['deleted']:
                parts.append('삭제됨' if n['deleted'] == '1' else '되살아남')
            if o['text'] != n['text']:
                parts.append('본문')
            if o['title'] != n['title']:
                parts.append('제목')
            if '0' not in (o['deleted'], n['deleted']):  # 양쪽 다 삭제 조문: 답변과 무관
                parts = []
            for c in others:
                if o[c] != n[c]:
                    diff.meta[c] = diff.meta.get(c, 0) + 1
        if parts:
            diff.answer.append(
                {
                    'article_id': aid,
                    'kind': '·'.join(parts),
                    'effective_date': (n if n is not None else o)['effective_date'],
                    'old': '' if o is None else o['text'],
                    'new': '' if n is None else n['text'],
                }
            )
    return diff


def pending(versions) -> list[dict]:
    """생성기가 시행 전이라 넣지 않은 조문 → [{date, doc, 공포번호, articles}] 시행일 순"""
    groups: dict[tuple, list[str]] = {}
    for v in versions:
        for no, date in v.get('pending') or []:
            groups.setdefault((date, v['doc'], str(int(v['공포번호']))), []).append(no)
    return [
        {'date': date, 'doc': doc, '공포번호': num, 'articles': arts}
        for (date, doc, num), arts in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]


@dataclass
class Result:
    repo: str
    asof: str
    head: str
    data: dict
    diff: Diff
    pending: list[dict]
    new: list[dict] | None
    report: Path | None = None
    dataset: Dataset = V4

    @property
    def same(self) -> bool:
        return not self.diff.answer


def check(repo, asof, data_dir=V4.source_dir, dataset=V4) -> dict:
    """비교 결과 (Result의 필드). 파일을 쓰지 않는다"""
    return check_with_files(repo, asof, data_dir, dataset)[0]


def check_with_files(repo, asof, data_dir=V4.source_dir, dataset=V4) -> tuple[dict, dict[str, bytes]]:
    """(비교 결과, 생성한 파일 {이름: 바이트}). lawbot-articles-update가 같은 생성 결과로 갱신한다"""
    if not (Path(repo) / 'HEAD').exists() and not (Path(repo) / '.git').exists():
        raise FileNotFoundError(
            f'legalize-kr 클론이 없습니다: {repo} (git clone --filter=blob:none --no-checkout https://github.com/legalize-kr/legalize-kr)'
        )
    _, combined = ba.csv_names(dataset.version)
    csv = Path(data_dir) / combined
    m = manifest.load(csv) or {}
    scope = dataset.scope
    options = {'scope': ba.load_scope(scope), 'version': dataset.version} if scope else {}
    files, versions, stats = ba.build(repo, asof, **options)
    new_df = pd.read_csv(io.BytesIO(files[combined]), encoding='utf-8-sig', dtype=str, keep_default_na=False)
    old_df = pd.read_csv(csv, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    ref = (m.get('source') or {}).get('ref')
    result = {
        'repo': str(repo),
        'dataset': dataset,
        'asof': asof,
        'head': stats['head'],
        'data': {
            'path': str(csv),
            'dataset_version': m.get('dataset_version'),
            'reference_date': m.get('reference_date'),
            'ref': ref,
        },
        'diff': compare(old_df, new_df),
        'pending': pending(versions),
        'new': commits_since(ba.Repo(repo), ref, stats['paths']),
    }
    return result, files


def summary_line(r: Result) -> str:
    n_pending = sum(len(p['articles']) for p in r.pending)
    nxt = r.pending[0]['date'] if r.pending else '-'
    new = '?' if r.new is None else len(r.new)
    line = (
        f'articles same={"yes" if r.same else "no"} changed={len(r.diff.answer)} '
        f'pending={n_pending} next={nxt} new={new}'
    )
    return line + (f' → {r.report}' if r.report else '')


def _short_diff(old: str, new: str) -> list[str]:
    lines = [
        x
        for x in difflib.unified_diff(old.split('\n'), new.split('\n'), lineterm='', n=0)
        if x[:1] in '+-' and not x.startswith(('+++', '---'))
    ]
    return lines[:DIFF_LINES] + ([f'… ({len(lines) - DIFF_LINES}줄 더)'] if len(lines) > DIFF_LINES else [])


def render(r: Result, next_steps: bool = True) -> str:
    """보고서 md. next_steps: 다르면 손으로 다시 만드는 명령 (자동 갱신 보고서에서는 뺌)"""
    d = r.data
    data = (
        f'`{d["path"]}` (데이터 {d["dataset_version"]} · 기준일 {d["reference_date"]} · 원문 {(d["ref"] or "?")[:8]})'
    )
    result = '**시행 중인 법과 같음**' if r.same else f'**답변 근거가 다른 조문 {len(r.diff.answer)}개**'
    md = [
        f'# 조문 데이터 점검 {r.asof}',
        '',
        f'- 기준일: {r.asof}에 시행 중인 조문 (legalize-kr `{r.head[:8]}`)',
        f'- 지금 데이터: {data}',
        f'- 결과: {result}',
        '',
    ]
    if r.diff.answer:
        md += ['## 답변 근거가 다른 조문', '', '| 조문 | 변화 | 시행일 |', '| --- | --- | --- |']
        md += [f'| {c["article_id"]} | {c["kind"]} | {c["effective_date"]} |' for c in r.diff.answer]
        md.append('')
        for c in r.diff.answer:
            lines = _short_diff(c['old'], c['new'])
            if lines:
                md += [f'### {c["article_id"]}', '', '```diff', *lines, '```', '']
    if r.diff.meta:
        md += ['## 그 밖의 열 변화', '', '- ' + ' · '.join(f'{k} {v}' for k, v in r.diff.meta.items()), '']
    md += ['## 시행 예정 (기준일 뒤)', '']
    if r.pending:
        md += ['| 시행일 | 문서 | 개정 | 조문 |', '| --- | --- | --- | --- |']
        md += [f'| {p["date"]} | {p["doc"]} | 제{p["공포번호"]}호 | {", ".join(p["articles"])} |' for p in r.pending]
    else:
        md.append('- 없음')
    md += ['', '## 데이터를 만든 뒤 들어온 개정', '']
    if r.new is None:
        md.append('- 알 수 없음 (매니페스트 `source.ref`가 없거나 클론에 그 커밋이 없음)')
    elif not r.new:
        md.append(f'- 없음 (`{(d["ref"] or "")[:8]}` 이후)')
    else:
        md += ['| 공포일 | 문서 | 개정 | 시행일 | 커밋 |', '| --- | --- | --- | --- | --- |']
        md += [
            f'| {c["date"]} | {c["doc"]} | 제{c["공포번호"]}호 | {c["시행일자"]} | {c["sha"][:8]} {c["subject"]} |'
            for c in r.new
        ]
    if not r.same and next_steps:
        _, combined = ba.csv_names(r.dataset.version)
        scope = f' --scope {r.dataset.scope} --version {r.dataset.version}' if r.dataset.scope else ''
        md += [
            '',
            '## 다음 할 일',
            '',
            '새 폴더에 만들고, 매니페스트를 쓰고, 본문이 바뀌었으니 val로 검색·답변을 확인한 뒤 웹앱 설정을 바꿔 재시작합니다.',
            '',
            '```zsh',
            f'uv run lawbot-build-articles --repo {r.repo} --asof {r.asof} --out-dir data/<새 버전>{scope}',
            f'uv run lawbot-reindex data/<새 버전>/{combined} --dataset-version <새 버전> '
            f'--reference-date {r.asof} --source-ref {r.head}',
            '```',
        ]
    return '\n'.join(md).rstrip('\n') + '\n'


def main(argv=None):
    p = argparse.ArgumentParser(description='조문 데이터 점검: 오늘 시행 중인 법과 지금 데이터 비교')
    p.add_argument('--repo', required=True, help='legalize-kr 클론 (git clone --filter=blob:none, bare도 됨)')
    p.add_argument('--asof', help='기준일 YYYY-MM-DD (기본 오늘, 한국 시간)')
    p.add_argument('--data-dir', type=Path, help='지금 데이터 폴더 (기본 data/<프로필>)')
    p.add_argument('--report-dir', type=Path, help='보고서 <기준일>.md를 쓸 폴더')
    a = p.parse_args(argv)
    asof = a.asof or today()
    try:
        dt.date.fromisoformat(asof)
        envfile.load()
        dataset = Dataset(deploy.profile_name())
        r = Result(**check(a.repo, asof, a.data_dir or dataset.source_dir, dataset))
    except Exception as e:  # 생성기 실패(부칙을 못 읽음 등)·git·파일 오류: 한 줄로 알리고 종료 코드 2
        print(f'articles error={" ".join(str(e).split())}')
        return 2
    if a.report_dir:
        a.report_dir.mkdir(parents=True, exist_ok=True)
        r.report = a.report_dir / f'{asof}.md'
        r.report.write_text(render(r), encoding='utf-8')
    for c in r.diff.answer:
        print(f'[다름] {c["article_id"]} {c["kind"]} (시행 {c["effective_date"]})')
    for x in r.pending[:5]:
        print(f'[시행 예정] {x["date"]} {x["doc"]} 제{x["공포번호"]}호 {", ".join(x["articles"])}')
    print(summary_line(r))
    return 0 if r.same else 1


if __name__ == '__main__':
    sys.exit(main())
