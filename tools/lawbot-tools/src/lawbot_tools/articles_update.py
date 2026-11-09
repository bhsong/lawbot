"""Refresh the configured article corpus from Legalize KR for the daily monitor.

The v4 live directory is data/live, and v5 uses data/live/v5. The generator uses
the common law scope and v5 article selections. Build and gate the candidate
before archiving and switching it.
The monitor contract is exit 0 for no change, 1 for an update, and 2 for failure.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from lawbot import corpus_store, deploy, envfile, manifest, paths
from lawbot.db.corpus_import import prepare_corpus
from lawbot.db.migrate import startup_path
from lawbot_common.logs import logger
from lawbot_tools import articles_check as ac
from lawbot_tools import build_articles as ba
from lawbot_tools import reindex

ROOT = paths.ROOT
MANIFESTS = ac.V4.manifests
READY_TIMEOUT = 180  # 초: 재시작 뒤 웹앱이 응답할 때까지
GATE_K = 10
LABEL = os.environ.get('LAWBOT_WEB_LAUNCHD_LABEL', 'com.lawbot.web')
log = logger('lawbot', 'lawbot')


class UpdateError(RuntimeError):
    pass


def _names(dataset):
    manifests = dataset.manifests
    return [*manifests, *manifests.values()]


# ── 웹앱·모델 서버·launchd (테스트에서 바꿔 끼움) ─────────────────────


def web_csv() -> Path:
    """Return the CSV selected by the web app settings."""
    return deploy.get_profile().articles


def web_port() -> int:
    from lawbot import security

    return security.server_port()


def make_retriever(csv: Path | bytes):
    """웹앱과 같은 방식의 검색기. 만들면서 임베딩 캐시(cache/embeddings)를 남겨 재시작한 웹앱이 그대로 쓴다"""
    from lawbot import settings
    from lawbot.api_client import Embedder, ModelServerClient, request_timeout, wait_ready
    from lawbot.embed_cache import EmbedCache
    from lawbot.retriever import Retriever

    cfg = settings.load()
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    wait_ready(client, cfg.server_wait)
    decree = cfg.decree.max_seq if cfg.decree else None
    cache = EmbedCache(cfg.embed_cache) if cfg.embed_cache is not None else None
    return Retriever(
        Embedder(client, cfg.profile.emb_model, query_timeout=request_timeout(cfg.read_timeout)),
        csv,
        cfg.profile.max_seq,
        decree_max_seq=decree,
        cache=cache,
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
        raise UpdateError(f'웹앱 kickstart 실패 (종료 코드 {k.returncode})')
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


def _archive_path(archive: Path, name: str, tag: str) -> Path:
    path = Path(name)
    return archive / f'{path.stem}_{tag}{path.suffix}'


def archive_current(live: Path, dataset: ac.Dataset) -> tuple[str, list[tuple[Path, Path]]]:
    """Copy a complete snapshot before replacing any active files."""
    archive = live / 'archive'
    archive.mkdir(exist_ok=True)
    _, combined = ba.csv_names(dataset.version)
    data = json.loads((live / dataset.manifests[combined]).read_text(encoding='utf-8'))
    date = tag = data['reference_date']
    names = _names(dataset)
    n = 1
    while any(_archive_path(archive, name, tag).exists() for name in names):
        n += 1
        tag = f'{date}_{n}'
    copied = []
    try:
        for name in names:
            src, dst = live / name, _archive_path(archive, name, tag)
            copied.append((src, dst))
            shutil.copyfile(src, dst)
        # The combined CSV timestamps the complete snapshot, independent of its source mtime.
        os.utime(_archive_path(archive, combined, tag), None)
    except OSError:
        for _, dst in copied:
            dst.unlink(missing_ok=True)
        raise
    return tag, copied


def restore(moved: list[tuple[Path, Path]]):
    for src, dst in moved:
        os.replace(dst, src)


def switch(live: Path, stage: Path, dataset: ac.Dataset) -> tuple[str, list[tuple[Path, Path]]]:
    tag, copied = archive_current(live, dataset)
    try:
        for name in _names(dataset):
            os.replace(stage / name, live / name)
    except OSError:
        restore(copied)
        raise
    return tag, copied


def do_restart(previous: list[tuple[Path, Path]]) -> str:
    """Restore the prior snapshot if the matching launchd service fails to start."""
    return _restart_or_restore(lambda _: restore(previous))


def _restart_or_restore(revert) -> str:
    try:
        ok, why = restart_web()
        if not ok:
            return f'skipped({why})'
        if wait_web(web_port()):
            return 'ok'
        raise UpdateError(f'웹앱이 {READY_TIMEOUT}초 안에 응답하지 않음')
    except UpdateError as e:
        revert(e)
        restarted, _ = restart_web()
        ready = restarted and wait_web(web_port())
        detail = '웹앱 응답 확인' if ready else '복구 뒤 웹앱 응답을 확인하지 못함'
        raise UpdateError(f'{e}. 이전 데이터로 되돌림 ({detail})') from e


def update(repo: str, asof: str, live: Path, report_dir: Path | None, restart: bool, dataset: ac.Dataset) -> int:
    if not all((live / n).is_file() for n in _names(dataset)):
        raise UpdateError(f'{live}가 비어 있습니다. 먼저 lawbot-articles-update --init을 실행하세요.')
    web = web_csv()
    if Path(web).resolve() not in [(live / name).resolve() for name in dataset.manifests]:
        prefix = f'LAWBOT_{dataset.version.upper()}'
        raise UpdateError(
            f'웹앱 조문 CSV가 {live}의 갱신 대상이 아닙니다 ({web}). {prefix}_DECREE_ARTICLES와 {prefix}_ARTICLES를 확인하세요.'
        )
    if dataset.scope:
        for name, configured in zip(dataset.manifests, deploy.article_paths(dataset.version), strict=True):
            if configured.resolve() != (live / name).resolve():
                raise UpdateError(f'v5 웹앱의 두 조문 CSV를 모두 {live}로 설정하세요.')
            if manifest.check(live / name) is None:
                raise UpdateError(f'매니페스트 없음: {name}')
    rdict, files = ac.check_with_files(repo, asof, live, dataset)
    r = ac.Result(**rdict)
    version = f'v5-live-{asof}' if dataset.scope else f'live-{asof}'
    report = report_dir / f'{asof}.md' if report_dir else None
    if r.same:
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(ac.render(r, next_steps=False), encoding='utf-8')
            r.report = report
        print('articles updated=no ' + ac.summary_line(r).removeprefix('articles '))
        return 0

    with tempfile.TemporaryDirectory(prefix='.staging-', dir=live) as folder:
        stage = Path(folder)
        for name, data in files.items():
            (stage / name).write_bytes(data)
        for name, man in dataset.manifests.items():
            errors, live_df = reindex.validate(stage / name)
            if errors:
                raise UpdateError(f'{name} 검사 실패: {"; ".join(errors)}')
            old = json.loads((live / man).read_text(encoding='utf-8'))
            args = Namespace(dataset_version=version, reference_date=asof, collected_date=ac.today(), source_ref=r.head)
            m = reindex.new_manifest(stage / name, live_df, old, args)
            (stage / man).write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        missing = gate(make_retriever(stage / Path(web).name), r.diff.answer, stage / Path(web).name)
        if missing:
            raise UpdateError(f'바뀐 조문이 자기 본문으로 상위 {GATE_K}개 안에 검색되지 않음: {", ".join(missing)}')
        tag, moved = switch(live, stage, dataset)

    restarted = do_restart(moved) if restart else 'no'
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        md = ac.render(r, next_steps=False) + '\n'.join(
            [
                '',
                '## 갱신',
                '',
                f'- 새 데이터: `{live}` (데이터 {version} · 기준일 {asof} · 원문 {r.head[:8]})',
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


def init(live: Path, dataset: ac.Dataset) -> int:
    names = _names(dataset)
    if any((live / n).exists() for n in names):
        raise UpdateError(f'이미 {live}에 조문 데이터가 있습니다 (되돌리려면 --rollback)')
    created = []
    try:
        for name in names:
            dst = live / name
            created.append(dst)
            shutil.copy2(dataset.source_dir / name, dst)
    except OSError:
        for dst in created:
            dst.unlink(missing_ok=True)
        raise
    print(f'articles init {dataset.source_dir} → {live}')
    return 0


def rollback(live: Path, restart: bool, dataset: ac.Dataset) -> int:
    archive = live / 'archive'
    _, combined = ba.csv_names(dataset.version)
    stem = Path(combined).stem
    sets = list(archive.glob(f'{stem}_*.csv'))
    if not sets:
        raise UpdateError(f'되돌릴 데이터가 없습니다 ({archive})')
    latest = max(sets, key=lambda p: (p.stat().st_mtime_ns, p.name))
    tag = latest.stem.removeprefix(f'{stem}_')
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=live) as folder:
        stage = Path(folder)
        for name in _names(dataset):
            src = _archive_path(archive, name, tag)
            if not src.is_file():
                raise UpdateError(f'보관본 {tag}의 파일이 모자랍니다')
            shutil.copy2(src, stage / name)
        if dataset.scope:
            for name in dataset.manifests:
                if manifest.check(stage / name) is None:
                    raise UpdateError(f'보관본 {tag}의 매니페스트가 없습니다 ({name})')
        current, moved = switch(live, stage, dataset)
    restarted = do_restart(moved) if restart else 'no'
    print(f'articles rollback restored={tag} archived={current} restart={restarted}')
    return 0


def _prepare_release(stage, files, dataset, current, result):
    for name in dataset.manifests:
        (stage / name).write_bytes(files[name])
    validated = {}
    for name in dataset.manifests:
        errors, live_df = reindex.validate(stage / name)
        if errors:
            raise UpdateError(f'{name} 검사 실패: {"; ".join(errors)}')
        validated[name] = live_df
    args = Namespace(
        dataset_version=f'{dataset.version}-live-{result.asof}',
        reference_date=result.asof,
        collected_date=ac.today(),
        source_ref=result.head,
    )
    for (name, filename), old in zip(dataset.manifests.items(), (current.law, current.combined), strict=True):
        data = reindex.new_manifest(stage / name, validated[name], old.manifest, args)
        (stage / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return prepare_corpus(stage, dataset.version, args.dataset_version)


def _cache_assessment(db, profile, notes):
    from lawbot import cache_carry
    from lawbot.cache_store import translate_sqlite_errors

    try:
        with translate_sqlite_errors():
            counts = cache_carry.transition(db, profile)
            if counts is not None:
                notes.append(cache_carry.summary(counts))
    except Exception as exc:  # Optional cache assessment must not undo a valid corpus activation.
        log.warning('캐시 판정 실패 (%s)', type(exc).__name__)
        notes.append('캐시 판정 실패')


def _restart_db(db, profile, current, previous, notes):
    def revert(error):
        corpus_store.rollback_release(db, profile, current, previous, restart_error=str(error))
        _cache_assessment(db, profile, notes)

    return _restart_or_restore(revert)


def _report_assessment(report, md, notes):
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(md + ''.join(f'\n- {note}\n' for note in notes), encoding='utf-8')
    for note in notes:
        print(note)


def _record_failed_quietly(db, profile, release_id, previous, error):
    """Try to record the failure without replacing the original exception."""
    try:
        corpus_store.record_failed(db, profile, release_id, previous, error)
    except Exception as exc:
        log.warning('경고: 릴리스 %s 실패 이벤트를 기록하지 못했습니다 (%s)', release_id or '-', type(exc).__name__)


def _publish_release(db, dataset, current, result, files):
    release_id = None
    try:
        with tempfile.TemporaryDirectory(prefix='lawbot-articles-') as folder:
            stage = Path(folder)
            corpus = _prepare_release(stage, files, dataset, current, result)
            release_id = corpus_store.stage_release(db, corpus, current.release_id)
            part = corpus.combined if deploy.decree_enabled() else corpus.law
            missing = gate(make_retriever(files[part.csv.path.name]), result.diff.answer, part.csv.path)
            if missing:
                raise UpdateError(f'바뀐 조문이 자기 본문으로 상위 {GATE_K}개 안에 검색되지 않음: {", ".join(missing)}')
        corpus_store.validate_and_activate(db, dataset.version, release_id, current.release_id)
    except BaseException as exc:  # Audit cleanup only; always re-raised.
        _record_failed_quietly(db, dataset.version, release_id, current.release_id, exc)
        raise
    return release_id


def _update_db(args, dataset, db, current):
    profile, previous = dataset.version, current.release_id
    if not args.repo:
        raise UpdateError('--repo가 필요합니다')
    asof = args.asof or ac.today()
    if date.fromisoformat(asof).isoformat() != asof:
        raise UpdateError('기준일은 YYYY-MM-DD 형식이어야 합니다.')
    try:
        rdict, files = ac.check_with_files(
            args.repo,
            asof,
            dataset=dataset,
            current=(current.combined.data, current.combined.manifest_bytes),
            label=f'릴리스 {previous}',
        )
    except BaseException as exc:  # Audit cleanup only; always re-raised.
        _record_failed_quietly(db, profile, None, previous, exc)
        raise
    result = ac.Result(**rdict)
    report = args.report_dir / f'{asof}.md' if args.report_dir else None
    if result.same:
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(ac.render(result, next_steps=False), encoding='utf-8')
            result.report = report
        print('articles updated=no ' + ac.summary_line(result).removeprefix('articles '))
        return 0

    release_id = _publish_release(db, dataset, current, result, files)
    notes = []
    _cache_assessment(db, profile, notes)
    restarted = 'failed'
    try:
        restarted = _restart_db(db, profile, release_id, previous, notes) if args.restart else 'no'
    finally:
        md = (ac.render(result, next_steps=False) if report else '') + '\n'.join(
            [
                '',
                '## 갱신',
                '',
                f'- 새 데이터: 릴리스 `{release_id}` (데이터 {profile}-live-{asof} · 기준일 {asof} · 원문 {result.head[:8]})',
                f'- 보관: 이전 릴리스 `{previous}`',
                f'- 확인: 바뀐 조문이 모두 자기 본문으로 상위 {GATE_K}개 안에 검색됨',
                f'- 재시작: {restarted}',
                '- 되돌리기: `uv run lawbot-articles-update --rollback --restart`',
                '',
            ]
        )
        _report_assessment(report, md, notes)
    print(
        f'articles updated=yes asof={asof} changed={len(result.diff.answer)} archive={previous} restart={restarted}'
        + (f' → {report}' if report else '')
    )
    return 1


def _run_db(args, dataset):
    db = startup_path(os.environ['LAWBOT_DB'].strip())
    profile = dataset.version
    with (db.parent / '.articles-update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError('같은 조문 폴더의 갱신 또는 복구가 진행 중입니다') from exc
        if args.init:
            release_id = corpus_store.initialize_release(db, dataset.source_dir, profile)
            print(f'articles init {dataset.source_dir} → {release_id}')
            return 0
        current = corpus_store.active_release(db, profile)
        if args.rollback:
            previous = corpus_store.previous_release(db, profile, current.release_id)
            corpus_store.rollback_release(db, profile, current.release_id, previous)
            notes = []
            _cache_assessment(db, profile, notes)
            restarted = 'failed'
            try:
                restarted = _restart_db(db, profile, previous, current.release_id, notes) if args.restart else 'no'
            finally:
                report = args.report_dir / f'{ac.today()}-rollback.md' if args.report_dir and notes else None
                _report_assessment(report, f'## 코퍼스 롤백\n\n- 대상 `{previous}`\n- 재시작 {restarted}\n', notes)
            print(f'articles rollback restored={previous} archived={current.release_id} restart={restarted}')
            return 0
        return _update_db(args, dataset, db, current)


def main(argv=None):
    p = argparse.ArgumentParser(
        description='조문 데이터 자동 갱신: 점검 → 생성 → 임베딩 → 확인 → 아카이브·전환 → 재시작'
    )
    p.add_argument('--repo', help='legalize-kr 클론 (갱신할 때)')
    p.add_argument('--asof', help='기준일 YYYY-MM-DD (기본 오늘, 한국 시간)')
    p.add_argument('--live-dir', type=Path, help='웹앱이 읽는 조문 데이터 폴더 (v4 data/live, v5 data/live/v5)')
    p.add_argument('--report-dir', type=Path, help='보고서 <기준일>.md를 쓸 폴더')
    p.add_argument('--restart', action='store_true', help='전환 뒤 웹앱(launchd com.lawbot.web) 재시작')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--init', action='store_true', help='프로필 기본 조문과 매니페스트로 live 데이터 초기화')
    g.add_argument('--rollback', action='store_true', help='바로 전 보관본으로 되돌림')
    a = p.parse_args(argv)
    try:
        envfile.load()
        dataset = ac.Dataset(deploy.profile_name())
        from lawbot.settings import corpus_mode

        if corpus_mode(os.environ) == 'db':
            return _run_db(a, dataset)
        live = a.live_dir or dataset.live_dir
        live.mkdir(parents=True, exist_ok=True)
        with (live / '.update.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise UpdateError('같은 조문 폴더의 갱신 또는 복구가 진행 중입니다') from e
            if a.init:
                return init(live, dataset)
            if a.rollback:
                return rollback(live, a.restart, dataset)
            if not a.repo:
                raise UpdateError('--repo가 필요합니다')
            return update(a.repo, a.asof or ac.today(), live, a.report_dir, a.restart, dataset)
    except Exception as e:  # 생성기·검사·모델 서버·파일 오류: 한 줄로, 지금 데이터 그대로
        print(f'articles updated=no error={" ".join(str(e).split())}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
