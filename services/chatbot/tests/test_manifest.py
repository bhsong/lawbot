# tests/test_manifest.py  →  법령 데이터 매니페스트 (manifest): 해시·조문 수 검사, 웹앱 시작 거부
# 실제 data/의 조문 CSV·매니페스트를 읽기만 한다 (바꾸는 경우는 tmp_path에 복사해서)
import json
import shutil

import pytest

import lawbot.service as service
from fakes import FakeLLM, FakeRetriever
from lawbot import check_deploy, manifest
from lawbot.paths import ARTICLES, V4_ARTICLES


def copy_v4(tmp_path, edit=None):
    """v4 조문 CSV와 매니페스트를 tmp_path로 복사. edit(csv 글, 매니페스트 dict)로 바꿀 수 있음"""
    csv, m = tmp_path / V4_ARTICLES.name, json.loads(manifest.path_for(V4_ARTICLES).read_text(encoding='utf-8'))
    data = V4_ARTICLES.read_bytes()  # 바이트 그대로 (BOM·줄바꿈이 바뀌면 해시가 달라짐)
    if edit:
        data = edit(data, m)
    csv.write_bytes(data)
    (tmp_path / manifest.NAME).write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    return csv


def one_char(data, m):
    return data.replace('사용자는'.encode(), '사용지는'.encode(), 1)  # 본문 한 글자만 바꿈 (조문 수는 그대로)


def levels(out):
    return {level for level, _ in out}


# ---- 레포의 매니페스트 ----
@pytest.mark.parametrize('articles,version,total', [(V4_ARTICLES, 'v4', 415), (ARTICLES, 'v1', 130)])
def test_repo_manifests_match_data(articles, version, total):
    m = manifest.load(articles)
    assert m['dataset_version'] == version and m['articles_total'] == total
    assert levels(manifest.verify(articles, m)) == {'OK'}
    assert manifest.check(articles) == m


def test_v4_manifest_contents():
    m = manifest.load(V4_ARTICLES)
    assert m['collected_date'] == '2026-10-20' and m['source']['name'] == 'Legalize KR'
    assert sum(m['articles_by_law'].values()) == m['articles_by_kind']['법률'] == 415
    assert m['trained_on'] == {'embedding': 'v4', 'fast_lora': 'v4'}


# ---- 불일치 ----
def test_hash_mismatch(tmp_path):
    csv = copy_v4(tmp_path, one_char)
    out = manifest.verify(csv, manifest.load(csv))
    assert [lv for lv, msg in out if 'sha256' in msg] == ['FAIL']
    assert [lv for lv, msg in out if '비삭제 조문' in msg] == ['OK']
    with pytest.raises(RuntimeError, match='매니페스트와 다릅니다'):
        manifest.check(csv)


def test_count_mismatch(tmp_path):
    def fewer(data, m):
        m['articles_total'], m['articles_by_law']['최저임금법'] = 414, 31
        return data

    csv = copy_v4(tmp_path, fewer)
    fails = [msg for lv, msg in manifest.verify(csv, manifest.load(csv)) if lv == 'FAIL']
    assert any('비삭제 조문 415개 (매니페스트 414)' in f for f in fails) and any('법령별' in f for f in fails)
    assert not any('sha256' in f for f in fails)


def test_kind_column_and_late_effective_date(tmp_path):
    csv = tmp_path / 'a.csv'
    csv.write_text(
        'law,article_id,title,deleted,text,kind,effective_date\n'
        '근로기준법,제1조,목적,0,본문,법률,2026-01-01\n'
        '근로기준법 시행령,제1조,목적,0,본문,시행령,2026-11-01\n',
        encoding='utf-8-sig',
    )
    m = {
        'articles_sha256': manifest.sha256(csv),
        'articles_total': 2,
        'collected_date': '2026-10-20',
        'articles_by_kind': {'법률': 1, '시행령': 1},
    }
    out = manifest.verify(csv, m)
    assert levels(out) == {'OK', 'WARN'}
    assert any('수집일(2026-10-20)보다 늦은 조문 1개' in msg for lv, msg in out if lv == 'WARN')
    m['articles_by_kind'] = {'법률': 2}
    assert any('종류별' in msg for lv, msg in manifest.verify(csv, m) if lv == 'FAIL')


def test_missing_manifest_only_warns(tmp_path, capsys):
    csv = tmp_path / 'v4.csv'
    shutil.copy(V4_ARTICLES, csv)
    assert manifest.load(csv) is None and manifest.check(csv) is None
    out = capsys.readouterr().out
    assert '경고' in out and '매니페스트' in out
    (tmp_path / manifest.NAME).write_text(json.dumps({'articles_file': 'other.csv'}), encoding='utf-8')
    assert manifest.load(csv) is None  # 다른 CSV를 설명하는 매니페스트는 적용하지 않음


# ---- check_deploy ----
def test_check_deploy_pass_and_fail(env, tmp_path):
    ok = check_deploy.run('v4')
    assert any(lv == 'INFO' and '매니페스트: dataset v4 · 기준일' in m for lv, m in ok)
    assert not any(lv == 'FAIL' and ('sha256' in m or '매니페스트' in m) for lv, m in ok)
    env.setenv('LAWBOT_V4_ARTICLES', str(copy_v4(tmp_path, one_char)))
    fails = [m for lv, m in check_deploy.run('v4') if lv == 'FAIL']
    assert any(m.startswith('조문 CSV sha256') for m in fails)


def test_check_deploy_warns_without_manifest(env, tmp_path):
    shutil.copy(V4_ARTICLES, tmp_path / 'v4.csv')
    env.setenv('LAWBOT_V4_ARTICLES', str(tmp_path / 'v4.csv'))
    out = check_deploy.run('v4')
    assert any(lv == 'WARN' and '매니페스트 없음' in m for lv, m in out)


# ---- 웹앱 시작 (load_app) ----
def test_load_app_refuses_changed_csv(env, tmp_path):
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_V4_ARTICLES', str(copy_v4(tmp_path, one_char)))
    FakeRetriever.created = 0
    with pytest.raises(RuntimeError, match='매니페스트와 다릅니다'):
        service.load_app()
    assert FakeRetriever.created == 0  # 모델 서버에 접속·인덱스를 만들기 전에 거부
    env.setenv('LAWBOT_V4_ARTICLES', str(copy_v4(tmp_path)))
    assert service.load_app().profile.name == 'v4'


def test_decree_csv_kinds():
    """812조문 CSV(kind 열: 법률·시행령·시행규칙)는 같은 폴더의 manifest_decree.json이 설명한다 (하위법령 첨부)
    기존 manifest.json은 그대로 415조문 CSV를 설명한다"""
    import pandas as pd

    decree = V4_ARTICLES.with_name('law_articles_v4_decree.csv')
    m = manifest.load(decree)
    assert m is not None and manifest.path_for(decree).name == 'manifest_decree.json'
    assert (
        manifest.load(V4_ARTICLES)['articles_total'] == 415 and manifest.path_for(V4_ARTICLES).name == 'manifest.json'
    )
    live = pd.read_csv(decree, encoding='utf-8-sig').query('deleted == 0')
    by_kind = live['kind'].value_counts().to_dict()
    assert by_kind == m['articles_by_kind'] == {'법률': 415, '시행령': 273, '시행규칙': 124}
    assert (m['articles_total'], m['dataset_version'], m['reference_date']) == (812, 'v4-decree', '2026-10-20')
    assert m['trained_on'] == {'embedding': 'v4', 'fast_lora': 'v4'}  # 임베딩·4B는 다시 학습하지 않음
    assert levels(manifest.verify(decree, m)) == {'OK'}
    assert (
        manifest.kinds_text(m) == '법률 415 · 시행령 273 · 시행규칙 124'
        and manifest.kinds_text(manifest.load(V4_ARTICLES)) == ''
    )
    bad = m | {'articles_by_kind': {'법률': 812}}  # v4 매니페스트를 그대로 복사한 경우
    assert any('종류별' in msg for lv, msg in manifest.verify(decree, bad) if lv == 'FAIL')
    bad = m | {'articles_total': 415}
    assert any('비삭제' in msg for lv, msg in manifest.verify(decree, bad) if lv == 'FAIL')


def test_manifest_extra_files(tmp_path):
    """폴더의 manifest_*.json 중 articles_file이 맞는 것. 같은 CSV를 설명하는 매니페스트가 둘이면 오류"""
    import json

    import pytest

    (tmp_path / 'a.csv').write_text('x'), (tmp_path / 'b.csv').write_text('x')
    (tmp_path / 'manifest.json').write_text(json.dumps({'articles_file': 'a.csv', 'dataset_version': 'A'}))
    (tmp_path / 'manifest_b.json').write_text(json.dumps({'articles_file': 'b.csv', 'dataset_version': 'B'}))
    assert manifest.load(tmp_path / 'a.csv')['dataset_version'] == 'A'
    assert manifest.load(tmp_path / 'b.csv')['dataset_version'] == 'B'
    assert manifest.load(tmp_path / 'c.csv') is None and manifest.path_for(tmp_path / 'c.csv').name == 'manifest.json'
    (tmp_path / 'manifest_b2.json').write_text(json.dumps({'articles_file': 'b.csv'}))
    with pytest.raises(RuntimeError, match='여러 개'):
        manifest.load(tmp_path / 'b.csv')
