# tests/test_reindex.py  →  재인덱싱 명령 (lawbot_tools.reindex): 새 조문 CSV 검사 → 매니페스트 → 임베딩 캐시 → 재시작 안내
import json
import shutil

import pandas as pd
import pytest

from lawbot import embed_cache, manifest
from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES
from lawbot_tools import reindex

E = dict(encoding='utf-8-sig')


@pytest.fixture
def folder(tmp_path):
    """data/v4처럼 조문 CSV와 매니페스트가 있는 폴더 (복사본)"""
    d = tmp_path / 'v4'
    d.mkdir()
    for f in (V4_ARTICLES, V4_ARTICLES.with_name('manifest.json')):
        shutil.copy(f, d / f.name)
    return d


def amend(csv, n_new=1):
    """개정 흉내: 조문 하나 본문을 바꾸고 근로기준법에 새 조문을 n_new개 추가"""
    d = pd.read_csv(csv, **E)
    d.loc[0, 'text'] = d.loc[0, 'text'] + ' (개정)'
    new = [
        d.iloc[0].to_dict() | {'article_id': f'근로기준법 제{900 + i}조', 'article_no': f'제{900 + i}조'}
        for i in range(n_new)
    ]
    pd.concat([d, pd.DataFrame(new)], ignore_index=True).to_csv(csv, index=False, encoding='utf-8-sig')


ARGS = ['--dataset-version', 'v4.1', '--reference-date', '2026-12-08', '--no-cache']


def test_amended_csv_gets_new_manifest_and_old_is_kept(folder, capsys):
    csv = folder / V4_ARTICLES.name
    before = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    amend(csv)
    with pytest.raises(RuntimeError, match='매니페스트와 다릅니다'):
        manifest.check(csv)  # 웹앱은 이 상태로 시작을 거부
    assert reindex.main([str(csv), *ARGS]) == 0
    m = manifest.check(csv)  # 이제 통과
    assert (m['dataset_version'], m['reference_date'], m['articles_total']) == ('v4.1', '2026-12-08', 416)
    assert m['articles_by_law']['근로기준법'] == 131 and m['source'] == before['source']
    assert m['trained_on'] == before['trained_on']  # 모델이 학습한 데이터 버전은 그대로
    assert json.loads((folder / 'manifest.json.prev').read_text(encoding='utf-8')) == before
    out = capsys.readouterr().out
    assert '근로기준법 +1' in out and 'launchctl kickstart' in out and '학습한 데이터(v4)와 다릅니다' in out


@pytest.mark.parametrize(
    'breaker,needle',
    [
        (lambda d: d.drop(columns=['text']), '열 없음'),
        (lambda d: pd.concat([d, d.iloc[:1]]), '중복 조문'),
        (lambda d: d.assign(article_no=d['article_no'].where(d.index != 0, '1조')), "'제N조' 형식"),
        (lambda d: d.assign(text=d['text'].where(d.index != 0, '')), '본문이 빈'),
    ],
)
def test_bad_csv_writes_nothing(folder, capsys, breaker, needle):
    csv = folder / V4_ARTICLES.name
    breaker(pd.read_csv(csv, **E)).to_csv(csv, index=False, encoding='utf-8-sig')
    before = (folder / 'manifest.json').read_bytes()
    assert reindex.main([str(csv), *ARGS]) == 2
    assert needle in capsys.readouterr().out
    assert (folder / 'manifest.json').read_bytes() == before and not (folder / 'manifest.json.prev').exists()


def test_check_only_writes_nothing(folder, capsys):
    csv = folder / V4_ARTICLES.name
    amend(csv)
    before = (folder / 'manifest.json').read_bytes()
    assert reindex.main([str(csv), *ARGS, '--check']) == 0
    assert (folder / 'manifest.json').read_bytes() == before and 'OK' in capsys.readouterr().out


def test_new_csv_name_gets_its_own_manifest(folder):
    csv = folder / 'articles_v5.csv'
    shutil.copy(folder / V4_ARTICLES.name, csv)
    assert reindex.main([str(csv), *ARGS]) == 0
    assert (folder / 'manifest_articles_v5.json').is_file() and manifest.check(csv)['articles_file'] == csv.name
    assert manifest.load(folder / V4_ARTICLES.name)['dataset_version'] == 'v4'  # 원래 매니페스트는 그대로


def test_builds_embedding_cache_with_model_server(folder, env, tmp_path, model_server):
    # 캐시를 미리 만들어 두면 웹앱 재시작 때 확인용 1개만 임베딩한다
    from conftest import TEST_TOKEN  # noqa: F401 (conftest의 가짜 모델 서버 설정)

    csv = folder / V4_ARTICLES.name
    amend(csv)
    env.setenv('LAWBOT_EMBED_CACHE', '1')
    env.setattr(embed_cache, 'CACHE_DIR', tmp_path / 'cache')
    env.setattr(reindex, 'ModelServerClient', model_server)
    assert reindex.main([str(csv), '--dataset-version', 'v4.1', '--reference-date', '2026-12-08']) == 0
    files = list((tmp_path / 'cache').glob('*.npz'))
    assert len(files) == 1
    calls = [c for c in model_server.instances[-1].calls if c[0] == 'embed']
    assert [c[2] for c in calls] == [416, 1]  # 전체 + 확인용


def test_decree_csv(tmp_path):
    d = tmp_path / 'v4'
    d.mkdir()
    for f in (V4_DECREE_ARTICLES, V4_DECREE_ARTICLES.with_name('manifest_decree.json')):
        shutil.copy(f, d / f.name)
    assert reindex.main([str(d / V4_DECREE_ARTICLES.name), *ARGS]) == 0
    m = manifest.check(d / V4_DECREE_ARTICLES.name)
    assert m['articles_by_kind'] == {'법률': 415, '시행령': 273, '시행규칙': 124} and 'articles_by_law' not in m
    assert (d / 'manifest_decree.json.prev').is_file()


def test_check_deploy_follows_new_manifest(folder, env):
    # 조문이 늘어난 CSV도 재인덱싱 뒤에는 check_deploy가 통과 (기대 조문 수는 매니페스트에서)
    from lawbot import check_deploy, deploy

    csv = folder / V4_ARTICLES.name
    amend(csv)
    env.setenv('LAWBOT_V4_ARTICLES', str(csv))
    p = deploy.get_profile()
    out = []
    check_deploy.check_articles(p, out)
    assert out[0][0] == 'FAIL' and '(기대 415)' in out[0][1]
    assert reindex.main([str(csv), *ARGS]) == 0
    out = []
    check_deploy.check_articles(p, out)
    assert out[0][0] == 'OK' and '416개 (기대 416)' in out[0][1]
