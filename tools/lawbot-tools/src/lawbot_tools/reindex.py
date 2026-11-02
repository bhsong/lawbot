# src/lawbot_tools/reindex.py  →  uv run lawbot-reindex <새 조문 CSV> --dataset-version v4.1 --reference-date 2026-12-08
# 법 개정이 반영된 조문 CSV를 웹앱에 넣는 입구 (CSV를 만드는 일은 하지 않음: docs/data-provenance.md, 다음 작업)
#   1. CSV 검사 (열·중복·조문 번호 형식·빈 본문·종류). 실패하면 아무것도 쓰지 않고 종료 코드 2
#   2. 매니페스트 새로 쓰기 (데이터 버전·기준일·조문 수·sha256). 그 CSV의 이전 매니페스트는 <이름>.json.prev로 보관 (manifest_*.json 찾기에 걸리지 않게)
#   3. 모델 서버로 조문 임베딩 캐시를 미리 만든다 (웹앱 재시작 때 확인용 1개만 임베딩, lawbot.embed_cache)
#   4. 웹앱 재시작 안내 (직접 재시작하지 않음)
# 캐시는 이 명령을 실행한 레포 폴더의 cache/에 생긴다: 운영에 넣을 때는 운영 worktree에서 실행
import argparse
import datetime
import json
import shutil
from pathlib import Path

import pandas as pd

from lawbot import deploy, manifest, settings
from lawbot.api_client import Embedder, ModelServerClient, wait_ready
from lawbot.check_deploy import COLUMNS, DECREE_COLUMNS
from lawbot.embed_cache import EmbedCache
from lawbot.retriever import Retriever

E = dict(encoding='utf-8-sig')
KINDS = ('법률', '시행령', '시행규칙')
NUMBER = r'제\d+조(?:의\d+)?'
DEFAULT_SOURCE = {'name': 'Legalize KR', 'url': 'https://github.com/legalize-kr/legalize-kr', 'ref': None}


def validate(csv):
    """(오류 목록, 비삭제 조문 DataFrame)"""
    if not csv.is_file():
        return [f'조문 CSV 없음: {csv}'], None
    d = pd.read_csv(csv, **E)
    need = COLUMNS | (DECREE_COLUMNS if 'kind' in d else set())
    if need - set(d.columns):
        return [f'열 없음 {sorted(need - set(d.columns))} (있는 열: {list(d.columns)})'], None
    errors = []
    if not d['deleted'].isin([0, 1]).all():
        errors.append('deleted 열은 0 또는 1이어야 합니다')
    live = d[d['deleted'] == 0]
    nums = (live['article_no'] if 'article_no' in live else live['article_id']).astype(str)
    key = live['law'].astype(str) + ' ' + nums + (' ' + live['kind'].astype(str) if 'kind' in live else '')
    if key.duplicated().any():
        errors.append(f'중복 조문 {int(key.duplicated(keep=False).sum())}행: {key[key.duplicated()].head().tolist()}')
    bad = nums[~nums.str.fullmatch(NUMBER)]
    if len(bad):
        errors.append(f"조문 번호가 '제N조' 형식이 아님 {len(bad)}행: {bad.head().tolist()}")
    empty = live[live['text'].fillna('').astype(str).str.strip() == '']
    if len(empty):
        errors.append(f'본문이 빈 조문 {len(empty)}행: {(empty["law"] + " " + empty["article_id"]).head().tolist()}')
    if 'kind' in live and not live['kind'].isin(KINDS).all():
        errors.append(f'kind는 {KINDS} 중 하나: {sorted(set(live["kind"]) - set(KINDS))}')
    return errors, live


def new_manifest(csv, live, old, args):
    by_kind = live['kind'].value_counts().to_dict() if 'kind' in live else {manifest.DEFAULT_KIND: len(live)}
    m = {
        'dataset_version': args.dataset_version,
        'reference_date': args.reference_date,
        'collected_date': args.collected_date or datetime.date.today().isoformat(),
        'source': (old or {}).get('source') or DEFAULT_SOURCE,
        'articles_file': csv.name,
        'articles_sha256': manifest.sha256(csv),
        'articles_total': len(live),
    }
    if 'kind' not in live:
        m['articles_by_law'] = live['law'].value_counts().to_dict()
    m['articles_by_kind'] = by_kind
    m['trained_on'] = (old or {}).get('trained_on') or {}
    return m


def changes(old, new):
    """'근로기준법 +1 · 최저임금법 -2' (법령별, 없으면 종류별)"""
    field = 'articles_by_law' if 'articles_by_law' in new else 'articles_by_kind'
    a, b = (old or {}).get(field) or {}, new[field]
    diff = {k: b.get(k, 0) - a.get(k, 0) for k in sorted(set(a) | set(b))}
    return ' · '.join(f'{k} {v:+d}' for k, v in diff.items() if v) or '조문 수 변화 없음'


def manifest_path(csv):
    """그 CSV를 설명하는 매니페스트가 있으면 그 파일, 없으면 manifest.json(없을 때) 또는 manifest_<이름>.json"""
    found = manifest.load(csv)
    if found is not None:
        return manifest.path_for(csv), found
    default = csv.parent / manifest.NAME
    return (default if not default.exists() else csv.parent / f'manifest_{csv.stem}.json'), None


def build_cache(csv, live):
    cfg = settings.load()
    if cfg.embed_cache is None:
        print('[reindex] 임베딩 캐시 꺼짐 (LAWBOT_EMBED_CACHE=0): 웹앱이 시작할 때 전체를 임베딩합니다')
        return
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    wait_ready(client, cfg.server_wait)
    decree = deploy.DECREE_MAX_SEQ if 'kind' in live else None
    Retriever(
        Embedder(client, cfg.profile.emb_model),
        csv,
        cfg.profile.max_seq,
        decree_max_seq=decree,
        cache=EmbedCache(cfg.embed_cache),
    )
    print(f'[reindex] 임베딩 캐시 준비 ({cfg.embed_cache}, 모델 {cfg.profile.emb_model})')


def main(argv=None):
    ap = argparse.ArgumentParser(description='새 조문 CSV → 검사·매니페스트·임베딩 캐시 (웹앱 재시작 전 준비)')
    ap.add_argument('csv', type=Path, help='새 조문 CSV (웹앱이 읽는 경로, 예: data/v4/articles_v4.csv)')
    ap.add_argument('--dataset-version', required=True, help='새 데이터 버전 (예: v4.1)')
    ap.add_argument('--reference-date', required=True, help='법령 기준일 YYYY-MM-DD')
    ap.add_argument('--collected-date', help='수집일 (기본 오늘)')
    ap.add_argument('--check', action='store_true', help='검사만 (아무것도 쓰지 않음)')
    ap.add_argument('--no-cache', action='store_true', help='임베딩 캐시를 만들지 않음 (모델 서버에 접속하지 않음)')
    args = ap.parse_args(argv)
    csv = args.csv.resolve()
    errors, live = validate(csv)
    if errors:
        for e in errors:
            print(f'[FAIL] {e}')
        print('[reindex] 검사 실패: 아무것도 쓰지 않았습니다')
        return 2
    assert live is not None
    path, old = manifest_path(csv)
    m = new_manifest(csv, live, old, args)
    print(f'[OK  ] 비삭제 조문 {len(live)}개 · sha256 {m["articles_sha256"][:12]} · {changes(old, m)}')
    trained = m['trained_on'].get('embedding')
    if trained and trained != m['dataset_version']:
        print(
            f'[WARN] 임베딩·4B가 학습한 데이터({trained})와 다릅니다: 바뀐 조문의 검색·답변 품질은 val로 다시 확인하세요'
        )
    if args.check:
        print('[reindex] --check: 아무것도 쓰지 않았습니다')
        return 0
    if path.exists():
        shutil.copy(path, path.with_name(f'{path.name}.prev'))
    path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[reindex] 매니페스트 {path}' + (f' (이전: {path.name}.prev)' if old else ''))
    if not args.no_cache:
        build_cache(csv, live)
    print(
        '[reindex] 웹앱을 다시 시작하세요: uv run python -m lawbot.check_deploy 후 launchctl kickstart -k gui/$(id -u)/com.lawbot.web'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
