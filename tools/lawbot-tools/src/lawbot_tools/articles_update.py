"""조문 데이터 자동 갱신 (계획: docs/archives/articles-auto-update.md)

    uv run lawbot-articles-update --init                                  # 처음 한 번: data/v4 → data/live
    uv run lawbot-articles-update --repo <legalize-kr 클론> [--asof 오늘] [--report-dir DIR] [--restart]
    uv run lawbot-articles-update --rollback [--restart]                  # 바로 전 데이터로

웹앱이 읽는 조문 CSV는 git 밖 data/live/ (운영 .env의 LAWBOT_V4_ARTICLES·LAWBOT_V4_DECREE_ARTICLES). data/v4는 학습 기준
데이터라 그대로 둔다. law-monitor daily(06:30)가 점검(checks)으로 --restart와 함께 부른다.

차례 (어디서든 실패하면 지금 데이터 그대로, 종료 코드 2):
  1. 웹앱 설정의 조문 CSV가 data/live 안인지
  2. 점검(articles_check): 기준일 생성 결과와 data/live가 같으면 끝 (종료 0)
  3. data/live/.staging/에 쓰고 검사(lawbot-reindex와 같음)·매니페스트(live-<기준일>, source.ref = legalize-kr HEAD)
  4. 웹앱이 읽을 CSV로 임베딩 캐시 (모델 서버)
  5. 확인: 답변 근거가 바뀐 조문이 자기 제목·본문으로 상위 10개 안에 검색되는지
  6. 지금 파일을 data/live/archive/<이름>_<이전 기준일>로 옮기고 새 파일로 전환
  7. --restart: launchd com.lawbot.web의 작업 폴더가 이 레포일 때만 재시작, 3분 안에 응답 없으면 되돌리고 다시 재시작
종료 코드 (monitor 점검 약속): 0 바뀐 것 없음, 1 갱신함, 2 실패. 마지막 줄 `articles updated=… `
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import httpx
import pandas as pd

from lawbot import paths
from lawbot_tools import articles_check as ac
from lawbot_tools import build_articles as ba
from lawbot_tools import reindex

ROOT = paths.ROOT
LIVE_DIR = paths.DATA / 'live'
SOURCE_DIR = paths.V4_DECREE_ARTICLES.parent  # --init이 복사하는 학습 기준 데이터
MANIFESTS = {ba.LAW_CSV: 'manifest.json', ba.DECREE_CSV: 'manifest_decree.json'}
READY_TIMEOUT = 180  # 초: 재시작 뒤 웹앱이 응답할 때까지
GATE_K = 10
LABEL = os.environ.get('LAWBOT_WEB_LAUNCHD_LABEL', 'com.lawbot.web')


class UpdateError(RuntimeError):
    pass


def _names():
    return [*MANIFESTS, *MANIFESTS.values()]


# ── 웹앱·모델 서버·launchd (테스트에서 바꿔 끼움) ─────────────────────


def web_csv() -> Path:
    """웹앱이 읽는 조문 CSV (.env의 LAWBOT_V4_DECREE_ARTICLES 또는 LAWBOT_V4_ARTICLES)"""
    from lawbot import deploy, envfile

    envfile.load()
    return deploy.get_profile().articles


def web_port() -> int:
    from lawbot import envfile, security

    envfile.load()
    return security.server_port()


def make_retriever(csv: Path):
    """웹앱과 같은 방식의 검색기. 만들면서 임베딩 캐시(cache/embeddings)를 남겨 재시작한 웹앱이 그대로 쓴다"""
    from lawbot import deploy, envfile, settings
    from lawbot.api_client import Embedder, ModelServerClient, wait_ready
    from lawbot.embed_cache import EmbedCache
    from lawbot.retriever import Retriever

    envfile.load()
    cfg = settings.load()
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    wait_ready(client, cfg.server_wait)
    decree = deploy.DECREE_MAX_SEQ if 'kind' in pd.read_csv(csv, nrows=0, encoding='utf-8-sig') else None
    cache = EmbedCache(cfg.embed_cache) if cfg.embed_cache is not None else None
    return Retriever(
        Embedder(client, cfg.profile.emb_model), csv, cfg.profile.max_seq, decree_max_seq=decree, cache=cache
    )


def launchd_workdir(text: str) -> str | None:
    m = re.search(r'^\s*working directory = (.+)$', text, re.M)
    return m[1].strip() if m else None


def restart_web() -> tuple[bool, str]:
    """(재시작했는지, 설명). launchd 작업의 작업 폴더가 이 레포가 아니면 하지 않는다 (개발 worktree에서 운영 웹앱 재시작 방지)"""
    target = f'gui/{os.getuid()}/{LABEL}'
    p = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return False, f'launchd 작업 없음: {LABEL}'
    wd = launchd_workdir(p.stdout)
    if wd is None or Path(wd).resolve() != Path(ROOT).resolve():
        return False, f'{LABEL}은 다른 폴더({wd})의 웹앱'
    k = subprocess.run(['launchctl', 'kickstart', '-k', target], capture_output=True, text=True, check=False)
    if k.returncode != 0:
        return False, f'kickstart 실패: {(k.stderr or k.stdout).strip()}'
    return True, f'kickstart {LABEL}'


def wait_web(port: int, timeout: float = READY_TIMEOUT) -> bool:
    """웹앱이 응답할 때까지 (조문 인덱스는 캐시라 금방, 모델 서버 준비 대기 포함)"""
    deadline = time.monotonic() + timeout
    time.sleep(3)
    while time.monotonic() < deadline:
        try:
            if httpx.get(f'http://127.0.0.1:{port}/', timeout=5, trust_env=False).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


# ── 단계 ─────────────────────────────────────────────────────────────


def gate(retriever, answer: list[dict], csv: Path) -> list[str]:
    """답변 근거가 바뀐 조문 중 자기 제목·본문으로 상위 GATE_K개 안에 검색되지 않는 것"""
    df = pd.read_csv(csv, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    rows = {r['article_id']: r for r in df[df['deleted'] == '0'].to_dict('records')}
    missing = []
    for c in answer:
        r = rows.get(c['article_id'])
        if r is None:  # 없어짐·삭제됨: 검색할 조문이 아님
            continue
        query = f'{r["title"]}\n{r["text"][:300]}'
        decree = r.get('kind', '법률') != '법률'
        hits = retriever.search_decrees(query, k=GATE_K) if decree else retriever.search(query, k=GATE_K)
        if not any(h['law'] == r['law'] and h['article_id'] == r['article_no'] for h in hits):
            missing.append(c['article_id'])
    return missing


def free_tag(archive: Path, date: str) -> str:
    tag, n = date, 1
    while any((archive / f'{Path(x).stem}_{tag}{Path(x).suffix}').exists() for x in _names()):
        n += 1
        tag = f'{date}_{n}'
    return tag


def _reference_date(live: Path) -> str:
    return json.loads((live / MANIFESTS[ba.DECREE_CSV]).read_text(encoding='utf-8'))['reference_date']


def archive_current(live: Path) -> tuple[str, list[tuple[Path, Path]]]:
    """지금 파일 4개를 archive/<이름>_<기준일>로 옮긴다 → (태그, [(원래 자리, 보관 자리)])"""
    archive = live / 'archive'
    archive.mkdir(exist_ok=True)
    tag = free_tag(archive, _reference_date(live))
    moved = []
    for name in _names():
        dst = archive / f'{Path(name).stem}_{tag}{Path(name).suffix}'
        shutil.move(live / name, dst)
        moved.append((live / name, dst))
    return tag, moved


def restore(moved: list[tuple[Path, Path]]):
    for src, dst in moved:
        if dst.exists():
            os.replace(dst, src)


def switch(live: Path, stage: Path) -> tuple[str, list[tuple[Path, Path]]]:
    tag, moved = archive_current(live)
    try:
        for name in _names():
            os.replace(stage / name, live / name)
    except OSError:
        restore(moved)
        raise
    return tag, moved


def do_restart(rollback_moved=None, live: Path | None = None) -> str:
    """재시작 → 'ok' 또는 'skipped(이유)'. 응답이 없으면 rollback_moved로 되돌리고 다시 재시작한 뒤 UpdateError"""
    ok, why = restart_web()
    if not ok:
        return f'skipped({why})'
    if wait_web(web_port()):
        return 'ok'
    if rollback_moved is not None and live is not None:
        restore(rollback_moved)
        restart_web()
        wait_web(web_port())
        raise UpdateError(f'웹앱이 {READY_TIMEOUT}초 안에 응답하지 않아 이전 데이터로 되돌림')
    raise UpdateError(f'웹앱이 {READY_TIMEOUT}초 안에 응답하지 않음')


def update(repo: str, asof: str, live: Path, report_dir: Path | None, restart: bool) -> int:
    if not all((live / n).is_file() for n in _names()):
        raise UpdateError(f'{live}가 비어 있습니다: 먼저 lawbot-articles-update --init')
    web = web_csv()
    if Path(web).resolve().parent != live.resolve():
        raise UpdateError(
            f'웹앱 조문 CSV가 {live} 안이 아닙니다 ({web}): .env의 LAWBOT_V4_DECREE_ARTICLES·LAWBOT_V4_ARTICLES를 {live}로'
        )
    rdict, files = ac.check_with_files(repo, asof, live)
    r = ac.Result(**rdict)
    report = report_dir / f'{asof}.md' if report_dir else None
    if r.same:
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(ac.render(r, next_steps=False), encoding='utf-8')
            r.report = report
        print('articles updated=no ' + ac.summary_line(r).removeprefix('articles '))
        return 0

    stage = live / '.staging'
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    try:
        for name, data in files.items():
            (stage / name).write_bytes(data)
        for name, man in MANIFESTS.items():
            errors, live_df = reindex.validate(stage / name)
            if errors:
                raise UpdateError(f'{name} 검사 실패: {"; ".join(errors)}')
            old = json.loads((live / man).read_text(encoding='utf-8'))
            args = Namespace(
                dataset_version=f'live-{asof}', reference_date=asof, collected_date=ac.today(), source_ref=r.head
            )
            m = reindex.new_manifest(stage / name, live_df, old, args)
            (stage / man).write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        missing = gate(make_retriever(stage / Path(web).name), r.diff.answer, stage / Path(web).name)
        if missing:
            raise UpdateError(f'바뀐 조문이 자기 본문으로 상위 {GATE_K}개 안에 검색되지 않음: {", ".join(missing)}')
        tag, moved = switch(live, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    restarted = do_restart(moved, live) if restart else 'no'
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        md = ac.render(r, next_steps=False) + '\n'.join(
            [
                '',
                '## 갱신',
                '',
                f'- 새 데이터: `{live}` (데이터 live-{asof} · 기준일 {asof} · 원문 {r.head[:8]})',
                '- 보관: ' + ', '.join(f'`archive/{dst.name}`' for _, dst in moved),
                f'- 확인: 바뀐 조문이 모두 자기 본문으로 상위 {GATE_K}개 안에 검색됨',
                f'- 재시작: {restarted}',
                '- 되돌리기: `uv run lawbot-articles-update --rollback --restart`',
                '',
            ]
        )
        report.write_text(md, encoding='utf-8')
    print(
        f'articles updated=yes asof={asof} changed={len(r.diff.answer)} archive={tag} restart={restarted}'
        + (f' → {report}' if report else '')
    )
    return 1


def init(live: Path) -> int:
    if any((live / n).exists() for n in _names()):
        raise UpdateError(f'이미 {live}에 조문 데이터가 있습니다 (되돌리려면 --rollback)')
    live.mkdir(parents=True, exist_ok=True)
    for n in _names():
        shutil.copy2(SOURCE_DIR / n, live / n)
    print(f'articles init {SOURCE_DIR} → {live}')
    return 0


def rollback(live: Path, restart: bool) -> int:
    archive = live / 'archive'
    sets = sorted(
        archive.glob(f'{Path(ba.DECREE_CSV).stem}_*.csv') if archive.is_dir() else [], key=lambda p: p.stat().st_mtime
    )
    if not sets:
        raise UpdateError(f'되돌릴 데이터가 없습니다 ({archive})')
    tag = sets[-1].stem.removeprefix(f'{Path(ba.DECREE_CSV).stem}_')
    chosen = [(live / n, archive / f'{Path(n).stem}_{tag}{Path(n).suffix}') for n in _names()]
    if not all(dst.exists() for _, dst in chosen):
        raise UpdateError(f'보관본 {tag}의 파일이 모자랍니다')
    current, moved = archive_current(live)
    try:
        restore(chosen)
    except OSError:
        restore(moved)
        raise
    restarted = do_restart() if restart else 'no'
    print(f'articles rollback restored={tag} archived={current} restart={restarted}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description='조문 데이터 자동 갱신: 점검 → 생성 → 임베딩 → 확인 → 아카이브·전환 → 재시작'
    )
    p.add_argument('--repo', help='legalize-kr 클론 (갱신할 때)')
    p.add_argument('--asof', help='기준일 YYYY-MM-DD (기본 오늘, 한국 시간)')
    p.add_argument('--live-dir', type=Path, default=LIVE_DIR, help='웹앱이 읽는 조문 데이터 폴더 (기본 data/live)')
    p.add_argument('--report-dir', type=Path, help='보고서 <기준일>.md를 쓸 폴더')
    p.add_argument('--restart', action='store_true', help='전환 뒤 웹앱(launchd com.lawbot.web) 재시작')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--init', action='store_true', help='data/v4 → data/live (처음 한 번)')
    g.add_argument('--rollback', action='store_true', help='바로 전 보관본으로 되돌림')
    a = p.parse_args(argv)
    try:
        if a.init:
            return init(a.live_dir)
        if a.rollback:
            return rollback(a.live_dir, a.restart)
        if not a.repo:
            raise UpdateError('--repo가 필요합니다')
        return update(a.repo, a.asof or ac.today(), a.live_dir, a.report_dir, a.restart)
    except Exception as e:  # 생성기·검사·모델 서버·파일 오류: 한 줄로, 지금 데이터 그대로
        print(f'articles updated=no error={" ".join(str(e).split())}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
