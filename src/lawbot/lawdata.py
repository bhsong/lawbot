# src/lawbot/lawdata.py  →  조문 데이터의 출처·시점 메타데이터와 검증 (모델 불필요)
# 데이터 점검: uv run python -m lawbot.lawdata [조문 CSV]  (검증·해시·메타데이터 일치 여부, 문제가 있으면 종료 코드 1)
# 조문 CSV 옆 <이름>.meta.json에 기록한 값만 쓰고, 기록에 없는 출처·공포일·시행일은 None으로 둔다.
# 본문의 <개정 …>·<신설 …>·삭제 <…> 날짜는 항별 개정 표시일 뿐 시행일이 아니므로 옮겨 적기만 한다.
# 이후 개정(later_amendments)은 확인한 날 기준의 기록이며, 부칙의 조문별 시행일을 계산하지 않는다
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import pandas as pd
from lawbot.paths import ARTICLES

COLUMNS = ('article_id', 'title', 'chapter', 'deleted', 'starter', 'text')
DATE_FIELDS = ('as_of', 'promulgated', 'effective')
STALE_DAYS = 180     # 기준일이 이보다 오래되면 그 뒤 개정 여부를 확인하지 않았다고 알림

_ID = re.compile(r'제\d+조(?:의\d+)?')
_DATE = re.compile(r'(\d{4})\.(\d{1,2})\.(\d{1,2})')
_MARK = re.compile(r'<(개정|신설) ([^<>]*)>|삭제 <([^<>]*)>')
_EXPIRY = re.compile(r'\[실효:[^\]]*\]')
_YEAR = re.compile(r'(?<!\d)((?:19|20)\d\d)\s*년')
_BEFORE = re.compile(r'개정\s*(?:전|이전)')


class DataError(ValueError):
    """조문 CSV를 색인할 수 없음 (열 누락, 조문 번호 중복·형식 오류, 빈 조문 등)"""


@dataclass(frozen=True)
class Snapshot:
    """조문 CSV 한 개의 출처·시점. 메타데이터가 없거나 파일 해시와 다르면 기록 값을 쓰지 않고 issues에 사유를 남긴다"""
    data_file: str
    sha256: str                      # 실제 파일 해시: 색인에 쓴 데이터의 식별자
    law_name: str | None = None
    as_of: str | None = None         # 기준일 (YYYY-MM-DD)
    source: str | None = None
    law_number: str | None = None    # 이 스냅샷이 반영한 법령 버전의 법률 번호·공포일·시행일 (기록이 있을 때만)
    promulgated: str | None = None
    effective: str | None = None
    checked_at: str | None = None    # 기준일 이후 개정을 마지막으로 확인한 날
    later: tuple = ()                # 그때 확인한 이후 개정 ({law_number, promulgated, effective, articles}, ...)
    issues: tuple = ()

    @property
    def verified(self):
        return not self.issues


def _iso(v):
    return None if v is None else date.fromisoformat(v).isoformat()


def _later(block):
    """later_amendments → (확인한 날, 개정 목록). 형식이 틀리면 KeyError·TypeError·ValueError"""
    if block is None:
        return None, ()
    acts = tuple({'law_number': str(a['law_number']), 'promulgated': _iso(a['promulgated']),
                  'effective': _iso(a.get('effective')), 'articles': tuple(a['articles'])}
                 for a in block['acts'])
    if not all(a['promulgated'] and all(isinstance(x, str) and _ID.fullmatch(x) for x in a['articles'])
               for a in acts):
        raise ValueError('공포일이나 조문 번호가 잘못됨')
    return _iso(block['checked_at']), acts


def read_meta(csv_path, sha256):
    path = Path(csv_path)
    meta_path = path.with_name(path.stem + '.meta.json')
    base = dict(data_file=path.name, sha256=sha256)
    if not meta_path.exists():
        return Snapshot(**base, issues=(f'출처 메타데이터 없음 ({meta_path.name})',))
    try:
        m = json.loads(meta_path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return Snapshot(**base, issues=(f'출처 메타데이터를 읽을 수 없음 ({meta_path.name}: {e})',))
    if not isinstance(m, dict):
        return Snapshot(**base, issues=(f'출처 메타데이터 형식 오류 ({meta_path.name})',))
    if m.get('sha256') != sha256:
        return Snapshot(**base, issues=('데이터 파일이 메타데이터에 기록된 해시와 다름 '
                                        '(파일을 바꾸고 메타데이터를 갱신하지 않았을 수 있음)',))
    issues, dates = [], {}
    for k in DATE_FIELDS:
        try:
            dates[k] = _iso(m.get(k))
        except (TypeError, ValueError):
            issues.append(f'메타데이터 {k} 날짜 형식 오류: {m[k]!r}')
            dates[k] = None
    try:
        checked_at, later = _later(m.get('later_amendments'))
    except (KeyError, TypeError, ValueError) as e:
        issues.append(f'메타데이터 later_amendments 형식 오류: {e!r}')
        checked_at, later = None, ()
    return Snapshot(**base, law_name=m.get('law_name'), source=m.get('source'), law_number=m.get('law_number'),
                    checked_at=checked_at, later=later, issues=tuple(issues), **dates)


def load_articles(csv_path):
    """조문 CSV를 검증해 (DataFrame, Snapshot)으로 돌려준다. 색인할 수 없는 데이터면 DataError"""
    raw = Path(csv_path).read_bytes()        # 해시와 내용이 같은 바이트에서 나오도록 한 번만 읽음
    law = pd.read_csv(io.BytesIO(raw), encoding='utf-8-sig')
    name = Path(csv_path).name
    if missing := [c for c in COLUMNS if c not in law]:
        raise DataError(f'{name}: 열 없음 {missing}')
    if not law['deleted'].isin([0, 1]).all():
        raise DataError(f'{name}: deleted는 0 또는 1이어야 함')
    if bad := [a for a in law['article_id'] if not (isinstance(a, str) and _ID.fullmatch(a))]:
        raise DataError(f'{name}: 조문 번호 형식 오류 {bad}')
    if dup := sorted(set(law.loc[law['article_id'].duplicated(), 'article_id'])):
        raise DataError(f'{name}: 조문 번호 중복 {dup}')
    live = law[law['deleted'] == 0]
    blank = lambda col: live[col].fillna('').astype(str).str.strip() == ''
    if live.empty:
        raise DataError(f'{name}: 색인할 조문이 없음')
    if empty := live.loc[blank('title') | blank('text'), 'article_id'].tolist():
        raise DataError(f'{name}: 제목이나 본문이 빈 조문 {empty}')
    return law, read_meta(csv_path, hashlib.sha256(raw).hexdigest())


def marks(text):
    """본문의 개정 표시 [(종류, 'YYYY-MM-DD'), ...] (적힌 순서). 종류는 개정·신설·삭제"""
    out = []
    for kind, dates, deleted in _MARK.findall(text):
        out += [(kind or '삭제', f'{int(y):04d}-{int(m):02d}-{int(d):02d}')
                for y, m, d in _DATE.findall(dates or deleted)]
    return out


def article_meta(article_id, title, text, snapshot):
    """검색 결과에 붙이는 조문 출처. (law_name, article_id, content_sha256)이 조문 버전을 구별한다"""
    m = marks(text)
    return {'law_name': snapshot.law_name, 'article_id': article_id, 'title': title,
            'data_file': snapshot.data_file, 'data_sha256': snapshot.sha256,
            'as_of': snapshot.as_of, 'source': snapshot.source,
            'content_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()[:16],
            'marks': m, 'last_marked': max((d for _, d in m), default=None),
            'expiry_notes': _EXPIRY.findall(text)}


def time_notes(question, metas, snapshot, today=None):
    """이 데이터로 조문의 시점·최신성을 확인할 수 없는 경우에만 안내 문장을 돌려준다 (해당 없으면 빈 목록)"""
    today, notes = today or date.today(), []
    if not snapshot.verified:
        notes.append('조문 데이터의 출처·기준일을 확인할 수 없습니다: ' + '; '.join(snapshot.issues))
    elif snapshot.as_of is None:
        notes.append('조문 데이터의 기준일이 기록되어 있지 않습니다.')
    elif (days := (today - date.fromisoformat(snapshot.checked_at or snapshot.as_of)).days) > STALE_DAYS:
        what = f'개정 여부를 마지막으로 확인한 날({snapshot.checked_at})' if snapshot.checked_at else \
            f'조문 데이터 기준일({snapshot.as_of})'
        notes.append(f'{what}이 {days}일 전입니다. 그 뒤 개정 여부는 확인하지 않았습니다.')
    years = sorted({int(y) for y in _YEAR.findall(question)})
    if years or _BEFORE.search(question):
        when = ', '.join(f'{y}년' for y in years) or '개정 전'
        basis = f'{snapshot.as_of} 기준' if snapshot.as_of else '기준일을 알 수 없는'
        notes.append(f'질문에 특정 시점({when})이 있습니다. 이 데이터는 {basis} 조문 한 가지뿐이라 '
                     '그 시점에 적용되는 조문인지 확인할 수 없습니다.')
        for m in metas if years else []:
            if later := [d for _, d in m['marks'] if int(d[:4]) >= years[0]]:
                notes.append(f"{m['article_id']}에는 {years[0]}년 이후 개정 표시(최근 {max(later)})가 있어 "
                             '그 시점의 내용과 다를 수 있습니다.')
    for m in metas:
        if acts := [a for a in snapshot.later if m['article_id'] in a['articles']]:
            by = ', '.join(f"{a['law_number']}, {a['promulgated']} 공포" for a in acts)
            notes.append(f"{m['article_id']}: 이 데이터 이후 공포된 개정({by})으로 본문이 바뀌었습니다"
                         f"({snapshot.checked_at} 확인). 위 본문은 개정 전 내용이고, 개정 시행 여부는 "
                         '부칙에 따라 조문마다 달라 확인하지 않았습니다.')
    notes += [f"{m['article_id']} 본문에 실효 표시가 있습니다: {' '.join(m['expiry_notes'])}. "
              '표시된 부분은 현재 적용되지 않을 수 있습니다.' for m in metas if m['expiry_notes']]
    return notes


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else ARTICLES
    try:
        law, snap = load_articles(path)
    except DataError as e:
        print(f'데이터 오류: {e}')
        return 1
    live = law[law['deleted'] == 0]
    expired = [a for a, x in zip(live['article_id'], live['text']) if _EXPIRY.search(x)]
    print(f"{snap.data_file}: 조문 {len(law)}개 (색인 {len(live)}개), sha256 {snap.sha256}")
    print(f"법령 {snap.law_name or '미기록'} {snap.law_number or '(법률 번호 미기록)'} · 기준일 {snap.as_of or '미기록'} · "
          f"출처 {snap.source or '미기록'} · 공포일 {snap.promulgated or '미기록'} · 시행일 {snap.effective or '미기록'}")
    print(f"이후 개정 확인일 {snap.checked_at or '미기록'}" + ''.join(
        f"\n- {a['law_number']} ({a['promulgated']} 공포, 시행일자 {a['effective'] or '미기록'}): "
        f"{', '.join(a['articles']) or '본문 변경 없음'}" for a in snap.later))
    print(f"본문 최근 개정 표시 {max((d for x in live['text'] for _, d in marks(x)), default='없음')} · "
          f"실효 표시가 있는 색인 조문 {expired or '없음'}")
    for issue in snap.issues:
        print(f'문제: {issue}')
    return 0 if snap.verified else 1


if __name__ == '__main__':
    raise SystemExit(main())
