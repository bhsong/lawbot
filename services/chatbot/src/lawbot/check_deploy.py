# lawbot/check_deploy.py  →  uv run python -m lawbot.check_deploy
# 웹앱이 쓸 조문 CSV·매니페스트·system·검색 설정과 웹앱 포트를 검사하고, 모델 서버에 접속해 필요한 모델 id가 있는지 본다
# 모델 폴더(양자화·어댑터·토크나이저) 검사는 모델 서버 쪽: uv run python -m lawbot_model_server.check
# 실패 항목이 있으면 종료 코드 1
import hashlib
import os
import sys

import pandas as pd

from lawbot import deploy, envfile, manifest, paths, security, service
from lawbot.deploy import get_profile

EXPECTED_ARTICLES = 415  # 법률 조문 수
EXPECTED_DECREE = {'시행령': 273, '시행규칙': 124}  # 하위법령을 켰을 때 812조문 CSV의 하위법령 수
DECREE_COLUMNS = {'kind', 'parent_law', 'article_no'}
COLUMNS = {'law', 'article_id', 'title', 'deleted', 'text'}


def check_articles(p, out):
    if not p.articles.is_file():
        return out.append(('FAIL', f'조문 CSV 없음: {p.articles}'))
    law = pd.read_csv(p.articles, encoding='utf-8-sig')
    missing = COLUMNS - set(law.columns)
    if missing:
        return out.append(('FAIL', f'조문 CSV 열 없음 {sorted(missing)} (있는 열: {list(law.columns)})'))
    live = law[law['deleted'] == 0]
    key = ['law', 'article_id']
    dup = live[live.duplicated(key, keep=False)]
    n_law = int((live['kind'] == '법률').sum()) if 'kind' in live else len(live)  # 검색기는 kind로 법률만 법률 인덱스에
    n_ok = n_law == EXPECTED_ARTICLES
    if 'kind' in live:  # 812조문 CSV (하위법령 첨부)
        out.append(
            (
                'OK' if n_ok else 'FAIL',
                f'비삭제 법률 조문 {n_law}개 (기대 {EXPECTED_ARTICLES}) · '
                f'전체 {len(live)}개 · sha256 {manifest.sha256(p.articles)[:12]}',
            )
        )
    else:
        out.append(
            (
                'OK' if n_ok else 'FAIL',
                f'비삭제 조문 {len(live)}개 (기대 {EXPECTED_ARTICLES}) · sha256 {manifest.sha256(p.articles)[:12]}',
            )
        )
    if len(dup):
        out.append(('FAIL', f'중복 조문 {len(dup)}행: {dup[key].head().values.tolist()}'))
    nums = live['article_no'] if 'article_no' in live else live['article_id']  # retriever와 같은 규칙
    bad = nums[~nums.astype(str).str.fullmatch(r'제\d+조(?:의\d+)?')]
    if len(bad):  # 법령명이 붙은 번호는 검색 문서·인용 대조에서 법령명이 두 번 들어감
        out.append(
            (
                'FAIL',
                f"조문 번호가 '제N조' 형식이 아님 {len(bad)}행: {bad.head().tolist()} "
                '(법령명이 들어 있으면 번호만 담은 article_no 열 추가)',
            )
        )
    out.append(('INFO', f'법령별 조문 수: {live["law"].value_counts().to_dict()}'))


def check_decree(p, out):
    """하위법령 첨부 설정과 812조문 CSV: 종류별 수, parent_law, 법률 415행이 415조문 CSV와 같은지(법률 검색이 같다는 전제)"""
    try:
        dc = deploy.decree(p)
    except ValueError as e:
        return out.append(('FAIL', f'하위법령 설정: {e}'))
    if dc is None:
        return out.append(('INFO', '시행령·시행규칙 첨부 꺼짐 (LAWBOT_DECREE_ENABLED=1이면 812조문 CSV도 검사)'))
    unknown = [k for k in dc.models if k not in {s.key for s in p.models}]
    out.append(
        (
            'OK',
            f'시행령·시행규칙 첨부 켜짐: τ {dc.tau:g} · 최대 {dc.k}개 · 후보 {dc.candidates}개 · '
            f'인코딩 max_seq {dc.max_seq} · 첨부 모델 {list(dc.models)} · 조문 CSV {p.articles.name}',
        )
    )
    if unknown:
        out.append(('INFO', f'첨부 모델 {unknown}은 이 기기에서 쓰지 않음 (LAWBOT_MODELS)'))
    if not p.articles.is_file():
        return
    law = pd.read_csv(p.articles, encoding='utf-8-sig')
    missing = DECREE_COLUMNS - set(law.columns)
    if missing:
        return out.append(
            ('FAIL', f'하위법령 CSV 열 없음 {sorted(missing)}: LAWBOT_V4_DECREE_ARTICLES가 812조문 CSV인지 확인')
        )
    live = law[law['deleted'] == 0]
    got = live.loc[live['kind'] != '법률', 'kind'].value_counts().to_dict()
    out.append(('OK' if got == EXPECTED_DECREE else 'FAIL', f'하위법령 조문 {got} (기대 {EXPECTED_DECREE})'))
    laws = set(live.loc[live['kind'] == '법률', 'law'])
    orphan = live[(live['kind'] != '법률') & ~live['parent_law'].isin(laws)]
    out.append(
        (
            'OK' if orphan.empty else 'FAIL',
            '하위법령 parent_law '
            + (
                '모두 7개 법률 중 하나'
                if orphan.empty
                else f'법률에 없음 {len(orphan)}행: {orphan["article_id"].head().tolist()}'
            ),
        )
    )
    base = paths.V4_ARTICLES
    if base.is_file():
        cols = ['law', 'article_no', 'title', 'text']
        a = live.loc[live['kind'] == '법률', cols].reset_index(drop=True)
        b = pd.read_csv(base, encoding='utf-8-sig').query('deleted == 0')[cols].reset_index(drop=True)
        same = a.equals(b)
        out.append(
            (
                'OK' if same else 'FAIL',
                f'법률 조문이 {base.name}와 '
                + ('같음 (법률 검색·임베딩 그대로)' if same else '다름: 하위법령을 켜면 법률 검색 결과가 바뀔 수 있음'),
            )
        )


def check_manifest(p, out):
    """조문 CSV가 매니페스트(조문 수·sha256)와 같은지. 웹앱(load_app)은 FAIL이면 시작을 거부한다"""
    if not p.articles.is_file():
        return  # check_articles가 FAIL
    m = manifest.load(p.articles)
    if m is None:
        return out.append(('WARN', f'매니페스트 없음: {manifest.path_for(p.articles)} (데이터 버전을 확인하지 않음)'))
    out.append(('INFO', f'매니페스트: {manifest.describe(m)}'))
    out.extend(manifest.verify(p.articles, m))


def check_system_file(name, f, out):
    if not f.is_file():
        return out.append(('FAIL', f'{name} system 파일 없음: {f} (학습 노트북의 system 원문을 글자 그대로 저장)'))
    text = f.read_text(encoding='utf-8').strip()
    out.append(
        (
            'OK',
            f'{name} system {len(text)}자 · sha256 {hashlib.sha256(text.encode()).hexdigest()[:12]} · '
            f'첫 줄: {text.splitlines()[0][:60]}',
        )
    )


def check_system(p, out):
    check_system_file('기본', p.system_file, out)
    for spec in p.models:  # 모델별 system (Pro 간결형)
        if spec.system_file:
            check_system_file(spec.label, spec.system_file, out)
        out.append(
            (
                'INFO',
                f'{spec.label}: 기본 답변 형식 {spec.fmt} · system '
                f'{spec.system_file.name if spec.system_file else "기본"}',
            )
        )


def check_reranker(p, out):
    """리랭커 설정 (켜져 있으면 후보 수와 모델 id. 모델 폴더는 모델 서버 점검)"""
    try:
        rr = deploy.reranker(p)
    except ValueError as e:
        return out.append(('FAIL', f'리랭커 설정: {e}'))
    if rr is None:
        return out.append(('INFO', '리랭커 꺼짐 (LAWBOT_RERANKER_ENABLED=1이면 모델 서버의 리랭커 모델도 확인)'))
    out.append(
        (
            'OK' if rr.candidates >= service.TOP_K else 'FAIL',
            f'리랭커 {rr.model}: 후보 {rr.candidates}개 → {service.TOP_K}개 (v4 실험 10개)',
        )
    )


def check_models(p, out):
    out.append(('INFO', f'임베딩 {p.emb_model} · 조문 인코딩 max_seq_length {p.max_seq}'))
    for spec in p.models:
        out.append(('INFO', f'{spec.label}: 모델 {spec.model}'))
    for spec in p.disabled:  # LAWBOT_MODELS에서 뺀 모델은 검사하지 않음 (화면에 비활성 표시)
        out.append(('INFO', f'{spec.label}: 이 기기에서 사용 안 함 (LAWBOT_MODELS)'))


def check_ports(out):
    try:
        out.append(
            (
                'OK',
                f'웹앱 주소 {security.SERVER_NAME}:{security.server_port()} '
                f'({"LAWBOT_PORT" if (os.environ.get("LAWBOT_PORT") or "").strip() else "기본값"})',
            )
        )
    except ValueError as e:
        out.append(('FAIL', f'웹앱 포트 설정: {e}'))


def check_model_server(p, out):
    """모델 서버 접속·토큰·준비, 필요한 모델 id (모델 서버가 떠 있어야 PASS). 생성 API 주소 설정도 확인"""
    try:
        url = security.model_server_url()
    except ValueError as e:
        return out.append(('FAIL', f'모델 서버 주소: {e}'))
    if url is None:
        return out.append(('FAIL', '모델 서버 주소 없음: .env에 LAWBOT_MODEL_SERVER와 LAWBOT_MODEL_TOKEN을 적으세요'))
    try:
        llm_url, _, external = security.llm_endpoint(url)
        out.append(
            ('OK', f'생성 API: {"외부 " + llm_url + " (질문이 이 맥 밖으로 나감)" if external else "모델 서버"}')
        )
    except (ValueError, RuntimeError) as e:
        return out.append(('FAIL', f'생성 API 설정: {e}'))
    try:
        client = service.ModelServerClient(url, security.model_token())  # 테스트는 service의 이름을 바꿔 끼움
        ready = client.ready()
    except (RuntimeError, ConnectionError) as e:  # 토큰 없음·불일치·접속 실패 (토큰 값은 출력하지 않음)
        return out.append(('FAIL', f'모델 서버 {url}: {e}'))
    out.append(('OK', f'모델 서버 {url} 접속·토큰 확인'))
    if not ready:
        return out.append(('FAIL', '모델 서버가 아직 모델을 불러오는 중입니다. 잠시 뒤 다시 확인하세요.'))
    available = client.models()
    try:
        rr = deploy.reranker(p)
    except ValueError:  # check_reranker가 FAIL로 알림
        rr = None
    missing = service.missing_models(available, p, rr, external)
    out.extend(('FAIL', f'모델 서버에 {kind} 모델 {mid} 없음 (LAWBOT_SERVE_*와 맞추기)') for kind, mid in missing)
    if not missing:
        out.append(('OK', '필요한 모델이 모델 서버에 모두 있음'))
    out.append(
        (
            'INFO',
            '모델 서버: '
            + ', '.join(f'{m["id"]}({m.get("type")}{", 상주" if m.get("loaded") else ""})' for m in available),
        )
    )


def run():
    p = get_profile()
    out = [('INFO', f'프로필 {p.name}: {p.title}')]
    check_articles(p, out)
    check_manifest(p, out)
    check_system(p, out)
    check_models(p, out)
    check_reranker(p, out)
    check_decree(p, out)
    check_ports(out)
    check_model_server(p, out)
    return out


def main():
    envfile.load()  # 웹앱과 같은 .env 설정으로 검사
    out = run()
    for level, msg in out:
        print(f'[{level:4}] {msg}')
    sys.exit(1 if any(level == 'FAIL' for level, _ in out) else 0)


if __name__ == '__main__':
    main()
