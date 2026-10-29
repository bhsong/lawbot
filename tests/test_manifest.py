# tests/test_manifest.py  →  법령 데이터 매니페스트 (manifest): 해시·조문 수 검사, 모델 서버 시작 거부, /info dataset 비교
# 실제 data/의 조문 CSV·매니페스트를 읽기만 한다 (바꾸는 경우는 tmp_path에 복사해서)
import json
import shutil
import types
import pytest
import lawbot.service as service
from lawbot import check_deploy, deploy, manifest, model_server, remote
from lawbot.paths import ARTICLES, V4_ARTICLES
from fakes import FakeLLM, FakeRetriever

def copy_v4(tmp_path, edit=None):
    """v4 조문 CSV와 매니페스트를 tmp_path로 복사. edit(csv 글, 매니페스트 dict)로 바꿀 수 있음"""
    csv, m = tmp_path / V4_ARTICLES.name, json.loads(manifest.path_for(V4_ARTICLES).read_text(encoding='utf-8'))
    data = V4_ARTICLES.read_bytes()                  # 바이트 그대로 (BOM·줄바꿈이 바뀌면 해시가 달라짐)
    if edit:
        data = edit(data, m)
    csv.write_bytes(data)
    (tmp_path / manifest.NAME).write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    return csv

def one_char(data, m):
    return data.replace('사용자는'.encode(), '사용지는'.encode(), 1)   # 본문 한 글자만 바꿈 (조문 수는 그대로)

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
    csv.write_text('law,article_id,title,deleted,text,kind,effective_date\n'
                   '근로기준법,제1조,목적,0,본문,법률,2026-01-01\n'
                   '근로기준법 시행령,제1조,목적,0,본문,시행령,2026-11-01\n', encoding='utf-8-sig')
    m = {'articles_sha256': manifest.sha256(csv), 'articles_total': 2, 'collected_date': '2026-10-20',
         'articles_by_kind': {'법률': 1, '시행령': 1}}
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
    assert manifest.load(csv) is None                 # 다른 CSV를 설명하는 매니페스트는 적용하지 않음

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

# ---- 모델 서버 시작 (로컬 모드 load_app) ----
def test_model_server_refuses_changed_csv(env, tmp_path):
    env.setattr(service, 'Retriever', FakeRetriever)
    env.setattr(service, 'LLM', FakeLLM)
    env.setenv('LAWBOT_PROFILE', 'v4')
    env.setenv('LAWBOT_V4_ARTICLES', str(copy_v4(tmp_path, one_char)))
    FakeRetriever.created = 0
    with pytest.raises(RuntimeError, match='매니페스트와 다릅니다'):
        service.load_app(local=True)
    assert FakeRetriever.created == 0                 # 검색기·모델을 올리기 전에 거부
    env.setenv('LAWBOT_V4_ARTICLES', str(copy_v4(tmp_path)))
    assert service.load_app(local=True).profile.name == 'v4'

# ---- /info의 dataset (선택 필드) ----
def fake_app(articles):
    p = deploy.get_profile('v4')
    return types.SimpleNamespace(profile=types.SimpleNamespace(name='v4', doc_style='v4', articles=articles),
                                 models=types.SimpleNamespace(specs={'fast': 0, 'pro': 0}, loaded={}, max_resident=2),
                                 reranker=None, retriever=types.SimpleNamespace(ids=[0] * 415)), p

def test_info_has_dataset(tmp_path):
    app, p = fake_app(V4_ARTICLES)
    info = model_server.info(app)
    assert info['api_version'] == 1 and info['dataset'] == {
        'dataset_version': 'v4', 'reference_date': '2026-10-20',
        'articles_sha256': manifest.load(V4_ARTICLES)['articles_sha256']}
    assert remote.check_info(info, p) == [] and remote.dataset_warnings(info, p) == []
    app, _ = fake_app(tmp_path / 'v4.csv')            # 서버 쪽 매니페스트 없음
    assert model_server.info(app)['dataset'] is None

def test_old_server_without_dataset_is_accepted():
    app, p = fake_app(V4_ARTICLES)
    info = model_server.info(app)
    del info['dataset']                               # 이전 버전 서버
    assert remote.check_info(info, p) == [] and remote.dataset_warnings(info, p) == []

def test_dataset_version_difference_warns():
    app, p = fake_app(V4_ARTICLES)
    info = model_server.info(app)
    info['dataset'] = info['dataset'] | {'dataset_version': 'v4.1'}
    assert remote.check_info(info, p) == []           # 시작은 막지 않음
    assert remote.dataset_warnings(info, p) == ['dataset_version: 서버 v4.1 / 웹앱 v4 (모델 서버와 웹앱의 레포 버전을 맞추세요)']
