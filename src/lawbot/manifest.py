# src/lawbot/manifest.py  →  법령 데이터 매니페스트 (조문 CSV의 버전·기준일·출처·조문 수·sha256)
# 매니페스트는 조문 CSV와 같은 폴더의 manifest.json (v4: data/v4/manifest.json, v1: data/manifest.json)
# 한 폴더에 조문 CSV가 여럿이면 manifest_<이름>.json을 더 둔다 (v4 812조문: data/v4/manifest_decree.json)
#   → articles_file이 그 CSV 이름인 매니페스트를 고른다 (manifest.json 먼저, 그다음 manifest_*.json 이름순)
# 검사하는 곳: 로컬 모드 load_app(= 모델 서버, 불일치면 실행 거부), check_deploy(PASS/FAIL), 모델 서버 /info의 dataset
# 검색 인덱스는 파일이 아니라 모델 서버가 시작할 때 만든다: 대신 임베딩·4B LoRA가 학습한 dataset_version을 기록한다
# 모델 라이브러리를 import하지 않는다 (원격 모드 웹앱도 웹앱 레포의 매니페스트를 읽어 서버 값과 비교)
import hashlib
import json

NAME = 'manifest.json'
DEFAULT_KIND = '법률'                # kind 열이 없는 조문 CSV는 모두 법률

def _candidates(articles):
    d = articles.parent
    return [p for p in [d / NAME] + sorted(d.glob('manifest_*.json')) if p.is_file()]

def _find(articles):
    """(경로, dict) 또는 None. 같은 CSV를 설명하는 매니페스트가 둘 이상이면 어느 쪽을 믿을지 몰라 오류"""
    found = []
    for p in _candidates(articles):
        m = json.loads(p.read_text(encoding='utf-8'))
        if m.get('articles_file') == articles.name:
            found.append((p, m))
    if len(found) > 1:
        raise RuntimeError(f'조문 CSV {articles.name}의 매니페스트가 여러 개입니다: {[p.name for p, _ in found]}')
    return found[0] if found else None

def path_for(articles):
    """그 CSV의 매니페스트 경로 (없으면 기본 manifest.json 경로: 안내 문구용)"""
    f = _find(articles)
    return f[0] if f else articles.parent / NAME

def load(articles):
    """조문 CSV(Path)에 해당하는 매니페스트 dict. 그 CSV를 설명하는 매니페스트가 없으면 None"""
    f = _find(articles)
    return f[1] if f else None

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def summary(m):
    """모델 서버 /info의 dataset 필드 (없으면 None): 웹앱 레포의 매니페스트와 비교하는 값"""
    if m is None:
        return None
    return {k: m.get(k) for k in ('dataset_version', 'reference_date', 'articles_sha256')}

def kinds_text(m):
    """'법률 415 · 시행령 273 · 시행규칙 124' (articles_by_kind에 법률 말고 다른 종류가 있을 때만, 아니면 '')"""
    by = (m or {}).get('articles_by_kind') or {}
    if set(by) <= {DEFAULT_KIND}:
        return ''
    order = [DEFAULT_KIND, '시행령', '시행규칙']
    return ' · '.join(f'{k} {by[k]}' for k in sorted(by, key=lambda k: (order.index(k) if k in order else 9, k)))

def verify(articles, m):
    """[(수준, 메시지)]. 수준: OK / FAIL(해시·조문 수 불일치) / WARN(시행일이 수집일보다 늦은 조문)"""
    import pandas as pd
    out = []
    digest = sha256(articles)
    out.append(('OK' if digest == m['articles_sha256'] else 'FAIL',
                f"조문 CSV sha256 {digest[:12]} (매니페스트 {m['articles_sha256'][:12]})"))
    law = pd.read_csv(articles, encoding='utf-8-sig')
    live = law[law['deleted'] == 0]
    out.append(('OK' if len(live) == m['articles_total'] else 'FAIL',
                f"비삭제 조문 {len(live)}개 (매니페스트 {m['articles_total']})"))
    if 'articles_by_law' in m:
        got = live['law'].value_counts().to_dict() if 'law' in live else {}
        out.append(('OK' if got == m['articles_by_law'] else 'FAIL',
                    '법령별 조문 수 ' + ('일치' if got == m['articles_by_law'] else f"다름: CSV {got} / 매니페스트 {m['articles_by_law']}")))
    if 'articles_by_kind' in m:
        got = live['kind'].value_counts().to_dict() if 'kind' in live else {DEFAULT_KIND: len(live)}
        out.append(('OK' if got == m['articles_by_kind'] else 'FAIL',
                    '종류별 조문 수 ' + ('일치' if got == m['articles_by_kind'] else f"다름: CSV {got} / 매니페스트 {m['articles_by_kind']}")))
    if m.get('collected_date') and 'effective_date' in live:
        late = live[live['effective_date'].astype(str) > m['collected_date']]
        if len(late):
            ids = (late['article_id'].astype(str)).head().tolist()
            out.append(('WARN', f"시행일이 수집일({m['collected_date']})보다 늦은 조문 {len(late)}개: {ids} (미래 시행 조문인지 확인)"))
    return out

def describe(m):
    """한 줄 설명 (check_deploy·로그)"""
    return (f"dataset {m['dataset_version']} · 기준일 {m['reference_date']}"
            + (f" · {kinds_text(m)}" if kinds_text(m) else '')
            + (f" · 수집일 {m['collected_date']}" if m.get('collected_date') else '')
            + (f" · 출처 {m['source']['name']}" if m.get('source') else ''))

def check(articles):
    """로컬 모드 load_app(모델 서버)용: 불일치면 RuntimeError, 매니페스트가 없으면 경고만 출력. 매니페스트 dict 또는 None"""
    m = load(articles)
    if m is None:
        print(f'[lawbot] 경고: 조문 CSV {articles.name}의 매니페스트({path_for(articles)})가 없어 데이터 버전을 확인하지 않습니다')
        return None
    out = verify(articles, m)
    fails = [msg for level, msg in out if level == 'FAIL']
    if fails:
        raise RuntimeError(f'조문 CSV가 매니페스트와 다릅니다 ({articles}):\n- ' + '\n- '.join(fails)
                           + '\nCSV를 바꿨다면 manifest.json도 새 dataset_version으로 갱신하세요. '
                             'uv run python -m lawbot.check_deploy로 확인')
    for level, msg in out:
        if level == 'WARN':
            print(f'[lawbot] 경고: {msg}')
    return m
