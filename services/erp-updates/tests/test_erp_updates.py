"""Collect, review and export ERP reference items from synthetic monitor repositories."""

import hashlib
import json
from pathlib import Path

import pytest

import erp_repos as fx
from erp_updates import cli

PDF = b'%PDF-1.7\nsynthetic\n%%EOF\n'


@pytest.fixture
def env(tmp_path):
    repos = fx.publish(tmp_path, fx.baseline())
    config = tmp_path / 'sources.yaml'
    config.write_text(fx.CONFIG, encoding='utf-8')
    data = tmp_path / 'erp-data'

    def run(*args: str) -> int:
        return cli.main(['--data', str(data), '--config', str(config), *args])

    return tmp_path, repos, run


def listing(run, capsys) -> list[dict]:
    capsys.readouterr()
    assert run('list', '--json') == 0
    return json.loads(capsys.readouterr().out)


def by_key(rows: list[dict]) -> dict[str, dict]:
    return {row['sourceKey']: row for row in rows}


def export(run, tmp_path: Path) -> dict:
    out = tmp_path / 'public' / 'erp_calendar_public.json'
    assert run('export', '--out', str(out)) == 0
    return json.loads(out.read_text(encoding='utf-8'))


def approve_all(run, rows):
    for row in rows:
        assert run('review', 'approve', row['id'], '--reviewer', 'tester') == 0


def test_collects_one_item_per_matching_source_and_ignores_others(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    rows = by_key(listing(run, capsys))
    assert set(rows) == {'INS-01', 'INS-03', 'TAX-01', 'INS-02H'}
    assert all(row['state'] == 'pending' and row['versions'] == 1 for row in rows.values())
    assert rows['INS-01']['title'] == '가상 연금 상한액'
    assert rows['TAX-01']['title'] == '가상 간이세액표(제1조 관련)'
    assert rows['INS-02H']['title'] == '가상건강보험법 시행령 제44조(보험료율)'


def test_recollecting_same_content_adds_no_version(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    assert run('collect', '--repos', str(repos)) == 0
    assert {row['versions'] for row in listing(run, capsys)} == {1}


def test_unapproved_items_stay_out_of_the_feed(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    rows = listing(run, capsys)
    assert export(run, tmp_path)['items'] == []
    approve_all(run, rows[:1])
    feed = export(run, tmp_path)
    assert [item['id'] for item in feed['items']] == [rows[0]['id']]


def test_changed_source_creates_pending_version_hidden_until_approved(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    approve_all(run, listing(run, capsys))
    assert len(export(run, tmp_path)['items']) == 4

    trees = fx.baseline()
    trees['admrule-kr'][fx.PENSION] = trees['admrule-kr'][fx.PENSION].replace('900천원', '950천원')
    fx.publish(tmp_path, {'admrule-kr': trees['admrule-kr']})
    assert run('collect', '--repos', str(repos)) == 0
    rows = by_key(listing(run, capsys))
    assert rows['INS-01']['versions'] == 2 and rows['INS-01']['state'] == 'pending'
    assert 'INS-01' not in {item['sourceKey'] for item in export(run, tmp_path)['items']}

    assert run('review', 'approve', rows['INS-01']['id'], '--reviewer', 'tester') == 0
    item = by_key(export(run, tmp_path)['items'])['INS-01']
    assert '950천원' in item['excerpt']


def test_attachment_link_change_alone_is_not_a_new_version(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    trees = fx.baseline()
    for name in trees:
        trees[name] = {
            path: text.replace('flSeq=7', 'flSeq=8').replace('flSeq=5', 'flSeq=6') for path, text in trees[name].items()
        }
    fx.publish(tmp_path, trees)
    assert run('collect', '--repos', str(repos)) == 0
    assert {row['versions'] for row in listing(run, capsys)} == {1}


def test_feed_dates_separate_effective_from_current_text(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    approve_all(run, listing(run, capsys))
    items = by_key(export(run, tmp_path)['items'])
    assert items['INS-01']['effectiveFrom'] == '2026-07-01'
    assert items['INS-01']['issuedNo'] == '2026-1'
    assert items['INS-01']['excerpt'] == '1. 하한액 : 100천원\n2. 상한액 : 900천원'
    for key in ('TAX-01', 'INS-02H'):
        assert items[key]['effectiveFrom'] is None
    assert items['TAX-01']['asOf'] == '2026-10-01'
    assert items['INS-02H']['excerpt'] == '① 보험료율은 1만분의 719로 한다.'


def test_feed_lists_related_laws_and_history_without_private_fields(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    approve_all(run, listing(run, capsys))
    feed = export(run, tmp_path)
    assert feed['schemaVersion'] == 1
    assert feed['relatedLaws'] == ['가상건강보험법 시행령', '가상소득세법 시행령']
    history = [entry for entry in feed['history'] if entry['sourceKey'] == 'INS-01']
    commit = fx.head(repos, 'admrule-kr')[:12]
    assert [(entry['action'], entry['origin']) for entry in history] == [
        ('collected', f'admrule-kr@{commit}'),
        ('approved', None),
    ]
    assert all(entry['at'].endswith('+09:00') for entry in history)
    text = json.dumps(feed, ensure_ascii=False)
    assert str(tmp_path) not in text and 'tester' not in text


def test_attach_requires_listed_link_and_pdf_bytes(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    pdf = tmp_path / 'download.pdf'
    pdf.write_bytes(PDF)
    html = tmp_path / 'error.pdf'
    html.write_bytes(b'<html>error</html>')
    listed = 'https://law.example.invalid/f?flSeq=71003'

    assert run('attach', '--file', str(pdf), '--url', 'https://law.example.invalid/f?flSeq=999') == 1
    assert run('attach', '--file', str(html), '--url', listed) == 1
    assert run('attach', '--file', str(pdf), '--url', listed) == 0

    rows = by_key(listing(run, capsys))
    approve_all(run, [rows['TAX-01']])
    item = by_key(export(run, tmp_path)['items'])['TAX-01']
    assert item['attachments'] == [
        {
            'title': '가상 간이세액표(제1조 관련)',
            'pdfUrl': listed,
            'sha256': hashlib.sha256(PDF).hexdigest(),
            'bytes': len(PDF),
        }
    ]
    assert [entry['action'] for entry in export(run, tmp_path)['history']] == ['collected', 'attached', 'approved']


def test_export_replaces_feed_atomically(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    export(run, tmp_path)
    export(run, tmp_path)
    assert sorted(path.name for path in (tmp_path / 'public').iterdir()) == ['erp_calendar_public.json']


def test_missing_source_file_is_reported_without_stopping_others(env, capsys):
    tmp_path, repos, run = env
    config = tmp_path / 'sources.yaml'
    config.write_text(fx.CONFIG.replace('kr/가상건강보험법/시행령.md', 'kr/없는법/시행령.md'), encoding='utf-8')
    assert run('collect', '--repos', str(repos)) == 1
    assert 'INS-02H' in capsys.readouterr().err
    assert set(by_key(listing(run, capsys))) == {'INS-01', 'INS-03', 'TAX-01'}


def test_collect_leaves_monitor_repositories_unchanged(env):
    tmp_path, repos, run = env

    def snapshot():
        return {
            path.relative_to(repos): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(repos.rglob('*'))
            if path.is_file()
        }

    before = snapshot()
    assert run('collect', '--repos', str(repos)) == 0
    assert snapshot() == before


def test_link_reissue_updates_stored_links_without_a_new_version(env, capsys):
    tmp_path, repos, run = env
    assert run('collect', '--repos', str(repos)) == 0
    pdf = tmp_path / 'download.pdf'
    pdf.write_bytes(PDF)
    assert run('attach', '--file', str(pdf), '--url', 'https://law.example.invalid/f?flSeq=71003') == 0
    trees = fx.baseline()
    trees['legalize-kr'] = {path: text.replace('flSeq=71', 'flSeq=81') for path, text in trees['legalize-kr'].items()}
    fx.publish(tmp_path, {'legalize-kr': trees['legalize-kr']})
    assert run('collect', '--repos', str(repos)) == 0

    rows = by_key(listing(run, capsys))
    assert rows['TAX-01']['versions'] == 1
    approve_all(run, [rows['TAX-01']])
    new = 'https://law.example.invalid/f?flSeq=81003'
    item = by_key(export(run, tmp_path)['items'])['TAX-01']
    assert item['attachments'] == [
        {'title': '가상 간이세액표(제1조 관련)', 'pdfUrl': new, 'sha256': None, 'bytes': None}
    ]
    assert run('attach', '--file', str(pdf), '--url', new) == 0


def test_change_beyond_the_excerpt_limit_creates_a_new_version(env, capsys):
    tmp_path, repos, run = env
    trees = fx.baseline()
    long_body = '가' * 5000
    trees['admrule-kr'][fx.PENSION] = trees['admrule-kr'][fx.PENSION].replace(
        '900천원', '900천원\n' + long_body + '끝1'
    )
    fx.publish(tmp_path, {'admrule-kr': trees['admrule-kr']})
    assert run('collect', '--repos', str(repos)) == 0
    trees['admrule-kr'][fx.PENSION] = trees['admrule-kr'][fx.PENSION].replace('끝1', '끝2')
    fx.publish(tmp_path, {'admrule-kr': trees['admrule-kr']})
    assert run('collect', '--repos', str(repos)) == 0
    assert by_key(listing(run, capsys))['INS-01']['versions'] == 2


def test_reads_stay_on_the_commit_seen_at_start(env, capsys, monkeypatch):
    tmp_path, repos, run = env
    from erp_updates import sources

    listed = sources.Repo.paths

    def move_head_after_listing(self):
        result = listed(self)
        trees = fx.baseline()
        trees['admrule-kr'][fx.PENSION] = trees['admrule-kr'][fx.PENSION].replace('900천원', '999천원')
        fx.publish(tmp_path, {'admrule-kr': trees['admrule-kr']})
        return result

    monkeypatch.setattr(sources.Repo, 'paths', move_head_after_listing)
    first = fx.head(repos, 'admrule-kr')
    assert run('collect', '--repos', str(repos)) == 0
    monkeypatch.undo()
    approve_all(run, [by_key(listing(run, capsys))['INS-01']])
    feed = export(run, tmp_path)
    assert '900천원' in by_key(feed['items'])['INS-01']['excerpt']
    origins = {entry['origin'] for entry in feed['history'] if entry['sourceKey'] == 'INS-01' and entry['origin']}
    assert origins == {f'admrule-kr@{first[:12]}'}


def test_admrule_rule_without_any_match_is_a_source_error(env, capsys):
    tmp_path, repos, run = env
    config = tmp_path / 'sources.yaml'
    config.write_text(fx.CONFIG.replace('^가상 연금 상한액$', '^없는 고시$'), encoding='utf-8')
    assert run('collect', '--repos', str(repos)) == 1
    assert 'INS-01' in capsys.readouterr().err
