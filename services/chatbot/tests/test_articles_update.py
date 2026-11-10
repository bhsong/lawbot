# tests/test_articles_update.py  →  조문 데이터 자동 갱신 (lawbot_tools.articles_update, 계획 docs/archives/articles-auto-update.md)
# Exercise the update pipeline with synthetic corpora and stubbed search and restart.
import fcntl
import io
import json
import shutil
from unittest.mock import Mock

import pandas as pd
import pytest

from lawbot import manifest
from lawbot.paths import V4_ARTICLES
from lawbot_tools import articles_update as au
from lawbot_tools import build_articles as ba
from test_build_articles import commit
from test_build_articles import law_with_decree as law_with_decree

COLS = ba.DECREE_COLS
HEAD = 'd' * 40


def frame(texts: dict[str, str]):
    """{'근로기준법 제1조': 본문, '근로기준법 시행령 제1조': 본문} → 생성기와 같은 열"""
    out = []
    for aid, text in texts.items():
        law, no = aid.rsplit(' ', 1)
        kind = '시행령' if law.endswith('시행령') else '법률'
        parent = law.removesuffix(' 시행령')
        out.append(
            {
                'law': law,
                'article_id': aid,
                'article_no': no,
                'title': '제목',
                'chapter': None,
                'deleted': 0,
                'text': text,
                'effective_date': '2020-01-01',
                'related_ids': None,
                'kind': kind,
                'parent_law': parent,
                'parent_ids': None,
                'child_ids': None,
            }
        )
    return pd.DataFrame(out, columns=COLS)


OLD = frame({'근로기준법 제1조': '처음 목적', '근로기준법 제2조': '처음 정의', '근로기준법 시행령 제1조': '영 목적'})
NEW = frame({'근로기준법 제1조': '바뀐 목적', '근로기준법 제2조': '처음 정의', '근로기준법 시행령 제1조': '영 목적'})


def files_of(df):
    law = df[df['kind'] == '법률'][ba.LAW_COLS]
    return {ba.LAW_CSV: ba.to_csv_bytes(law), ba.DECREE_CSV: ba.to_csv_bytes(df)}


def write_set(folder, df, ref_date='2026-10-20', source_ref='c' * 40):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files_of(df).items():
        (folder / name).write_bytes(data)
    for csv, man in au.MANIFESTS.items():
        m = {
            'dataset_version': 'v4',
            'reference_date': ref_date,
            'collected_date': ref_date,
            'source': {'name': 'Legalize KR', 'url': 'u', 'ref': source_ref},
            'articles_file': csv,
            'articles_sha256': manifest.sha256(folder / csv),
            'trained_on': {'embedding': 'v4'},
        }
        (folder / man).write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')


class World:
    """가짜 생성기·웹앱 설정·검색기·재시작. calls에 순서를 남긴다"""

    def __init__(self, monkeypatch, live, new_df, *, found=True, ready=True):
        self.calls, self.found, self.ready = [], found, ready
        (live.parent / 'HEAD').write_text('ref: refs/heads/main\n')  # 가짜 클론 (--repo = tmp_path)
        monkeypatch.setattr(au.ac.ba, 'build', self.build)
        monkeypatch.setattr(au.ac, 'commits_since', lambda repo, ref, paths: [])
        monkeypatch.setattr(au, 'web_csv', lambda: live / ba.DECREE_CSV)
        monkeypatch.setattr(au, 'web_port', lambda: 12345)
        monkeypatch.setattr(au, 'make_retriever', self.retriever)
        monkeypatch.setattr(au, 'restart_web', self.restart)
        monkeypatch.setattr(au, 'wait_web', self.wait)
        self.new_df = new_df

    def build(self, repo, asof):
        self.calls.append('build')
        return files_of(self.new_df), [], {'head': HEAD, 'paths': {}}

    def retriever(self, csv):
        self.calls.append(f'embed {csv.name}')
        df = pd.read_csv(csv, encoding='utf-8-sig')
        hits = [{'law': r.law, 'article_id': r.article_no} for r in df.itertuples()] if self.found else []

        class R:
            def search(self, q, k=3):
                return [h for h in hits if not h['law'].endswith('시행령')][:k]

            def search_decrees(self, q, k=10):
                return [h for h in hits if h['law'].endswith('시행령')][:k]

        return R()

    def restart(self):
        self.calls.append('restart')
        return True, 'kickstart com.lawbot.web'

    def wait(self, port, timeout=au.READY_TIMEOUT):
        self.calls.append(f'wait {port}')
        return self.ready


@pytest.fixture
def live(tmp_path):
    d = tmp_path / 'live'
    write_set(d, OLD)
    return d


def run(live, tmp_path, *extra, repo=None):
    return au.main(
        [
            '--repo',
            str(repo or tmp_path),
            '--asof',
            '2026-11-27',
            '--live-dir',
            str(live),
            '--report-dir',
            str(tmp_path / 'reports'),
            *extra,
        ]
    )


def last_line(capsys):
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_init_copies_v4_and_refuses_twice(tmp_path, capsys):
    live = tmp_path / 'live'
    assert au.main(['--init', '--live-dir', str(live)]) == 0
    for name in (*au.MANIFESTS, *au.MANIFESTS.values()):
        assert (live / name).read_bytes() == (V4_ARTICLES.parent / name).read_bytes()
    assert au.main(['--init', '--live-dir', str(live)]) == 2
    assert '이미' in last_line(capsys)


def test_same_data_exit_0_and_nothing_written(live, tmp_path, monkeypatch, capsys):
    w = World(monkeypatch, live, OLD)
    before = {p.name: p.read_bytes() for p in live.iterdir() if not p.name.startswith('.')}
    assert run(live, tmp_path, '--restart') == 0
    assert last_line(capsys).startswith('articles updated=no same=yes')
    assert {p.name: p.read_bytes() for p in live.iterdir() if not p.name.startswith('.')} == before
    assert w.calls == ['build']


def test_update_archives_switches_embeds_and_restarts(live, tmp_path, monkeypatch, capsys):
    w = World(monkeypatch, live, NEW)
    old = {name: (live / name).read_bytes() for name in au.MANIFESTS}
    assert run(live, tmp_path, '--restart') == 1
    line = last_line(capsys)
    assert line.startswith('articles updated=yes asof=2026-11-27 changed=1 archive=2026-10-20 restart=ok')
    assert line.endswith(str(tmp_path / 'reports' / '2026-11-27.md'))
    assert w.calls == ['build', f'embed {ba.DECREE_CSV}', 'restart', 'wait 12345']
    arch = live / 'archive'
    assert sorted(p.name for p in arch.iterdir()) == [
        'articles_v4_2026-10-20.csv',
        'law_articles_v4_decree_2026-10-20.csv',
        'manifest_2026-10-20.json',
        'manifest_decree_2026-10-20.json',
    ]
    assert (arch / 'law_articles_v4_decree_2026-10-20.csv').read_bytes() == old[ba.DECREE_CSV]
    assert (live / ba.DECREE_CSV).read_bytes() == files_of(NEW)[ba.DECREE_CSV]
    m = manifest.check(live / ba.DECREE_CSV)  # 웹앱과 같은 검사 통과
    assert (m['dataset_version'], m['reference_date'], m['source']['ref']) == ('live-2026-11-27', '2026-11-27', HEAD)
    assert m['trained_on'] == {'embedding': 'v4'}  # 모델이 학습한 데이터는 그대로
    assert not list(live.glob('.staging-*'))
    md = (tmp_path / 'reports' / '2026-11-27.md').read_text(encoding='utf-8')
    assert '## 갱신' in md and 'law_articles_v4_decree_2026-10-20.csv' in md and '재시작: ok' in md


def test_second_update_same_reference_date_gets_unique_archive_name(live, tmp_path, monkeypatch, capsys):
    World(monkeypatch, live, NEW)
    assert run(live, tmp_path) == 1
    write_set(live, OLD)  # 같은 기준일 파일을 다시 두고 갱신
    assert run(live, tmp_path) == 1
    names = sorted(p.name for p in (live / 'archive').iterdir())
    assert 'law_articles_v4_decree_2026-10-20_2.csv' in names and 'manifest_decree_2026-10-20_2.json' in names


def test_gate_failure_keeps_current_data(live, tmp_path, monkeypatch, capsys):
    w = World(monkeypatch, live, NEW, found=False)
    before = (live / ba.DECREE_CSV).read_bytes()
    assert run(live, tmp_path, '--restart') == 2
    line = last_line(capsys)
    assert '검색되지 않음' in line and '근로기준법 제1조' in line
    assert (live / ba.DECREE_CSV).read_bytes() == before and not (live / 'archive').exists()
    assert 'restart' not in w.calls and not list(live.glob('.staging-*'))


def test_update_keeps_live_data_when_required_decree_disappears(tmp_path, law_with_decree, env, capsys):
    repo, decree = law_with_decree
    files, _, stats = ba.build(repo.path, '2022-01-01')
    baseline = pd.read_csv(io.BytesIO(files[ba.DECREE_CSV]), encoding='utf-8-sig')
    assert '시험법 시행령' in set(baseline['law'])
    live = tmp_path / 'live'
    write_set(live, baseline, '2022-01-01', source_ref=stats['head'])
    before = {path.name: path.read_bytes() for path in live.iterdir()}
    env.setenv('LAWBOT_V4_ARTICLES', str(live / ba.LAW_CSV))
    env.setenv('LAWBOT_V4_DECREE_ARTICLES', str(live / ba.DECREE_CSV))
    retriever = Mock(return_value=None)
    restart = Mock(return_value=(False, 'synthetic restart'))
    env.setattr(au, 'make_retriever', retriever)
    env.setattr(au, 'restart_web', restart)
    (repo.path / decree).unlink()
    commit(repo.path, {}, '2023-01-01', '대통령령: 시험법 시행령 원문 삭제')

    assert run(live, tmp_path, '--restart', repo=repo.path) == 2
    line = last_line(capsys)
    assert line.startswith('articles updated=no error=시험법 시행령:')
    assert not (live / 'archive').exists()
    assert {path.name: path.read_bytes() for path in live.iterdir() if not path.name.startswith('.')} == before
    assert not list(live.glob('.staging-*'))
    retriever.assert_not_called()
    restart.assert_not_called()


def test_web_not_ready_rolls_back_and_restarts_again(live, tmp_path, monkeypatch, capsys):
    w = World(monkeypatch, live, NEW, ready=False)
    before = {name: (live / name).read_bytes() for name in (*au.MANIFESTS, *au.MANIFESTS.values())}
    assert run(live, tmp_path, '--restart') == 2
    assert '되돌림' in last_line(capsys)
    assert {name: (live / name).read_bytes() for name in before} == before
    assert w.calls.count('restart') == 2


def test_web_must_read_live_dir(live, tmp_path, monkeypatch, capsys):
    World(monkeypatch, live, NEW)
    monkeypatch.setattr(au, 'web_csv', lambda: tmp_path / 'v4' / ba.DECREE_CSV)
    assert run(live, tmp_path) == 2
    assert 'LAWBOT_V4_DECREE_ARTICLES' in last_line(capsys)


def test_empty_live_dir_asks_for_init(tmp_path, monkeypatch, capsys):
    live = tmp_path / 'live'
    World(monkeypatch, live, NEW)
    assert run(live, tmp_path) == 2
    assert '--init' in last_line(capsys)


def test_rollback_restores_previous_set(live, tmp_path, monkeypatch, capsys):
    w = World(monkeypatch, live, NEW)
    before = {name: (live / name).read_bytes() for name in (*au.MANIFESTS, *au.MANIFESTS.values())}
    assert run(live, tmp_path) == 1
    assert au.main(['--rollback', '--live-dir', str(live), '--restart']) == 0
    assert {name: (live / name).read_bytes() for name in before} == before
    assert 'restart' in w.calls
    assert '2026-11-27' in last_line(capsys)  # 되돌리며 보관한 새 데이터 이름


def test_launchd_workdir_guard():
    text = '\tstate = running\n\tprogram = /opt/homebrew/bin/uv\n\tworking directory = /Users/x/Work/lawbot-prod\n'
    assert au.launchd_workdir(text) == '/Users/x/Work/lawbot-prod'
    assert au.launchd_workdir('state = running\n') is None


def test_restart_skips_other_worktree(monkeypatch, tmp_path):
    monkeypatch.setattr(au, 'ROOT', tmp_path / 'lawbot')
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)

        class P:
            returncode, stdout, stderr = 0, '\tworking directory = /Users/x/Work/lawbot-prod\n', ''

        return P()

    monkeypatch.setattr(au.subprocess, 'run', fake)
    ok, why = au.restart_web()
    assert not ok and 'lawbot-prod' in why and all('kickstart' not in c for c in calls)
    monkeypatch.setattr(au, 'ROOT', au.Path('/Users/x/Work/lawbot-prod'))
    calls.clear()
    ok, why = au.restart_web()
    assert ok and any('kickstart' in c for c in calls)


def test_staging_is_removed_after_validation_error(live, tmp_path, monkeypatch, capsys):
    bad = NEW.assign(text=['', '처음 정의', '영 목적'])  # 빈 본문
    World(monkeypatch, live, bad)
    assert run(live, tmp_path) == 2
    assert '본문이 빈' in last_line(capsys) and not list(live.glob('.staging-*'))
    shutil.rmtree(live)


def test_v5_daily_update_uses_scope_and_restores_all_files(tmp_path, env, capsys):
    env.setenv('LAWBOT_PROFILE', 'v5')
    live = tmp_path / 'live-v5'
    v5_law, v5_decree = ba.csv_names('v5')
    env.setenv('LAWBOT_V5_ARTICLES', str(live / v5_law))
    env.setenv('LAWBOT_V5_DECREE_ARTICLES', str(live / v5_decree))
    env.setenv('LAWBOT_DECREE_ENABLED', '1')
    env.setenv('LAWBOT_RERANKER_ENABLED', '1')
    assert au.main(['--init', '--live-dir', str(live)]) == 0
    names = (v5_law, v5_decree, 'manifest.json', 'manifest_law_articles_v5_decree.json')
    before = {name: (live / name).read_bytes() for name in names}
    baseline = pd.read_csv(live / v5_decree, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    modified = baseline.copy()
    changed = '소득세법 제1조'
    assert changed in set(modified['article_id'])
    modified.loc[modified['article_id'] == changed, 'text'] = '갱신 시험용 개정 본문'
    (tmp_path / 'HEAD').write_text('ref: refs/heads/main\n')

    def build(repo, asof, *, scope, version):
        assert version == 'v5'
        assert scope == ba.load_scope(V4_ARTICLES.parent.parent / 'v5' / 'scope_tax.csv')
        return (
            {
                v5_law: ba.to_csv_bytes(modified.loc[modified['kind'] == '법률', ba.LAW_COLS]),
                v5_decree: ba.to_csv_bytes(modified),
            },
            [],
            {'head': HEAD, 'paths': {}},
        )

    class Search:
        def search(self, query, k):
            return [{'law': '소득세법', 'article_id': '제1조'}] if found else []

    def make_retriever(csv):
        assert csv.name == v5_decree
        assert au.web_csv() == live / v5_decree
        return Search()

    env.setattr(ba, 'build', build)
    env.setattr(au.ac, 'commits_since', lambda *args: [])
    env.setattr(au, 'make_retriever', make_retriever)
    found = False
    assert run(live, tmp_path) == 2
    assert '검색되지 않음' in last_line(capsys)
    assert {name: (live / name).read_bytes() for name in names} == before
    found = True
    assert run(live, tmp_path) == 1
    assert manifest.check(live / v5_decree)['reference_date'] == '2026-11-27'
    assert manifest.check(live / v5_law)['source']['ref'] == HEAD
    assert au.main(['--rollback', '--live-dir', str(live)]) == 0
    assert {name: (live / name).read_bytes() for name in names} == before


def test_update_rejects_overlapping_rollback(live, capsys):
    before = {name: (live / name).read_bytes() for name in (*au.MANIFESTS, *au.MANIFESTS.values())}
    with (live / '.update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert au.main(['--rollback', '--live-dir', str(live)]) == 2
    assert '갱신 또는 복구가 진행 중' in last_line(capsys)
    assert {name: (live / name).read_bytes() for name in before} == before
