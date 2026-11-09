"""Compare release-scoped article dependencies and carry reviewed cache rows forward."""

import argparse
import os
from pathlib import Path

from lawbot import corpus_store, deploy, envfile, settings
from lawbot.cache_seed import csv_rows, load_bundle, load_protection
from lawbot.cache_store import translate_sqlite_errors
from lawbot.exact_cache import PROFILE, digest, normalize
from lawbot.paths import ROOT
from lawbot.service import cache_snapshot

FIELDS = ('law', 'article_no', 'title', 'kind', 'text', 'deleted')


def article_rows(release):
    rows = csv_rows(release.combined.data)
    indexed = {row['article_id']: tuple(row[key] for key in FIELDS) for row in rows}
    if len(indexed) != len(rows):
        raise ValueError('조문 스냅샷에 중복 ID가 있습니다.')
    return indexed


def corpus_changes(old, new):
    return {
        key: (old.get(key), new.get(key)) for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)
    }


def classify(dependencies, old, changes, guard, old_text, shown):
    laws = {old[key][0] for key in dependencies if key in old}
    affected = [
        key
        for key, versions in changes.items()
        if key in dependencies or any(row is not None and row[0] in laws for row in versions)
    ]
    if guard:
        return 'pending', guard, affected
    if not dependencies or any(key not in old or old[key][-1] != '0' or key in changes for key in dependencies):
        return 'off', 'dependency_changed', affected
    if affected:
        return 'pending', 'related_law_changed', affected
    if shown is None or shown != old_text:
        return 'pending', 'display_changed' if shown is not None else 'render_unavailable', affected
    return 'keep', 'unchanged', affected


def bundle_path(env):
    value = env.get('LAWBOT_EXACT_CACHE_BUNDLE', '').strip()
    return (ROOT / Path(value).expanduser()).resolve() if value else None


def verified_inputs(db, bundle_file):
    if bundle_file is None:
        return None, {}
    bundle = load_bundle(bundle_file)
    return load_protection(db, bundle, []), {q['answer_sha256']: q['answer'] for q in bundle.candidates}


def allowed_question(protection, source_id, original):
    return (
        protection.v4_train.get(source_id) == digest(original) and digest(normalize(original)) not in protection.blocked
    )


def transition(db, profile, env=None):
    from lawbot.cache_release_store import apply_release

    env = os.environ if env is None else env
    value = env.get('LAWBOT_EXACT_CACHE_DB', '').strip()
    if not value or profile != PROFILE:
        return None
    path = settings.exact_cache_path(value, db)
    token = corpus_store.active_token(db, profile)
    release = corpus_store.active_release(db, profile)
    config = deploy.get_profile(env)
    snapshot = cache_snapshot(config, release, deploy.decree(config, env))
    if token is None or token[0] != release.release_id or snapshot is None:
        raise ValueError('캐시 판정용 활성 릴리스 또는 v5 Pro 설정을 확인할 수 없습니다.')
    return apply_release(path, db, token, release, snapshot, bundle_path(env))


def summary(counts):
    return f'캐시: 유지 {counts["keep"]} · 검토 대기 {counts["pending"]} · 끔 {counts["off"]}'


def main(argv=None):
    parser = argparse.ArgumentParser(description='현재 활성 DB 릴리스 기준으로 기존 캐시를 재판정합니다.')
    parser.parse_args(argv)
    envfile.load()
    try:
        with translate_sqlite_errors():
            if settings.corpus_mode(os.environ) != 'db':
                raise ValueError('DB 코퍼스 모드가 필요합니다.')
            from lawbot.db.migrate import startup_path

            counts = transition(startup_path(os.environ['LAWBOT_DB'].strip()), deploy.profile_name())
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f'캐시 판정 실패 ({type(exc).__name__})\n')
    print(summary(counts) if counts is not None else '캐시 미설정 또는 지원 범위 밖')


if __name__ == '__main__':
    main()
