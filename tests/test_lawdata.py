# tests/test_lawdata.py  →  조문 데이터 출처·시점 메타데이터(lawdata) 테스트
# 저장소의 data/를 읽기만 하고, 깨진 데이터·메타데이터는 tmp_path에 만들어 검사 (모델 불필요)
import hashlib
import json
import re
from datetime import date, timedelta
import pandas as pd
import pytest
from lawbot.lawdata import (STALE_DAYS, DataError, Snapshot, article_meta, load_articles, main, marks,
                            read_meta, time_notes)
from lawbot.paths import ARTICLES, EVAL

E = dict(encoding='utf-8-sig')
SHA = hashlib.sha256(ARTICLES.read_bytes()).hexdigest()
TODAY = date(2026, 10, 20)


@pytest.fixture(scope='module')
def loaded():
    return load_articles(ARTICLES)


@pytest.fixture(scope='module')
def metas(loaded):
    law, snap = loaded
    return {a: article_meta(a, t, x, snap) for a, t, x, d in
            zip(law['article_id'], law['title'], law['text'], law['deleted']) if d == 0}


def copy_with_meta(tmp_path, meta, csv_bytes=None):
    csv = tmp_path / 'law.csv'
    csv.write_bytes(csv_bytes if csv_bytes is not None else ARTICLES.read_bytes())
    if meta is not None:
        (tmp_path / 'law.meta.json').write_text(meta if isinstance(meta, str) else json.dumps(meta), encoding='utf-8')
    return csv


# 저장소 데이터의 메타데이터
def test_repo_snapshot_records_only_known_facts(loaded):
    law, snap = loaded
    assert snap.verified and snap.issues == ()
    assert (snap.data_file, snap.sha256) == (ARTICLES.name, SHA)
    assert (snap.law_name, snap.as_of) == ('근로기준법', '2026-10-01')
    # Legalize KR 커밋 ee38b100의 법률 제21373호와 대조해 확인한 값 (docs/data-provenance.md)
    assert (snap.source, snap.law_number) == ('Legalize KR', '법률 제21373호')
    assert (snap.promulgated, snap.effective, snap.checked_at) == ('2026-02-19', '2026-08-20', '2026-10-20')
    meta = json.loads(ARTICLES.with_name(ARTICLES.stem + '.meta.json').read_text(encoding='utf-8'))
    assert meta['amendment_history'] is None                 # 과거 버전 본문은 확보하지 않음
    assert meta['source_commit'] in meta['source_url']
    assert len(law) == 132 and (law['deleted'] == 0).sum() == 130


def test_repo_later_amendments(loaded, metas):
    _, snap = loaded
    assert [(a['law_number'], a['promulgated'], a['effective']) for a in snap.later] == [
        ('법률 제21472호', '2026-03-17', '2027-01-01'), ('법률 제21533호', '2026-04-07', '2026-10-08'),
        ('법률 제21784호', '2026-06-09', '2027-06-10'), ('법률 제21857호', '2026-08-04', '2026-10-02')]
    changed = {x for a in snap.later for x in a['articles']}
    assert len(changed) == 18 and '제54조' in changed and snap.later[3]['articles'] == ()
    assert changed - set(metas) == {'제44조의4'}            # 신설 조문: 이 데이터에 없어 검색되지 않음


def test_marks_parse_amendment_annotations():
    assert marks('① 용어 <개정 2018.3.20, 2019.1.15, 2020.5.26>\n② 내용 <신설 2024.10.22>') == [
        ('개정', '2018-03-20'), ('개정', '2019-01-15'), ('개정', '2020-05-26'), ('신설', '2024-10-22')]
    assert marks('② 삭제 <2009.5.21>') == [('삭제', '2009-05-21')]
    assert marks('표시 없는 조문') == []
    assert marks('제50조 <근로시간>') == []                 # 날짜 없는 꺾쇠는 표시가 아님


def test_marks_cover_every_dated_annotation_in_repo(loaded):
    law, _ = loaded
    for a, x in zip(law['article_id'], law['text']):
        dated = [d for b in re.findall(r'<[^<>]*>', x) for d in re.findall(r'\d{4}\.\d{1,2}\.\d{1,2}', b)]
        assert len(marks(x)) == len(dated), a


def test_article_meta_without_marks_has_no_dates(metas):
    m = metas['제1조']
    assert m['marks'] == [] and m['last_marked'] is None and m['expiry_notes'] == []
    assert metas['제2조']['last_marked'] == '2020-05-26'
    assert metas['제2조']['marks'][0] == ('개정', '2018-03-20')


def test_expiry_note_kept_for_indexed_article(metas, loaded):
    # 제53조 ③·⑥(30명 미만 사업장 추가 연장근로)은 실효 표시가 있지만 조문 자체는 색인 대상: 표시를 그대로 보존
    assert metas['제53조']['expiry_notes'] == ['[실효: 2018.3.20. 개정법 부칙에 따라 2022.12.31.까지 유효]',
                                              '[실효: 2022.12.31.까지 유효]']
    assert sum(bool(m['expiry_notes']) for m in metas.values()) == 1
    law, _ = loaded
    assert '제16조' not in metas and '[실효:' in law.set_index('article_id').loc['제16조', 'text']


def test_content_hash_distinguishes_versions():
    snap = Snapshot(data_file='a.csv', sha256='0' * 64, law_name='근로기준법', as_of='2026-10-01')
    old = article_meta('제26조', '해고의 예고', '30일 전에 예고', snap)
    new = article_meta('제26조', '해고의 예고', '30일 전에 예고 <개정 2019.1.15>', snap)
    assert old['content_sha256'] != new['content_sha256']
    assert old == article_meta('제26조', '해고의 예고', '30일 전에 예고', snap)
    assert len(old['content_sha256']) == 16


# 메타데이터가 없거나 맞지 않을 때: 기록 값을 쓰지 않고 사유를 남김
def test_missing_meta(tmp_path):
    snap = load_articles(copy_with_meta(tmp_path, None))[1]
    assert not snap.verified and '출처 메타데이터 없음 (law.meta.json)' in snap.issues[0]
    assert snap.sha256 == SHA and snap.as_of is None and snap.law_name is None


def test_hash_mismatch_drops_recorded_values(tmp_path):
    meta = {'law_name': '근로기준법', 'sha256': SHA, 'as_of': '2026-10-01'}
    changed = ARTICLES.read_bytes().replace('30일'.encode(), '60일'.encode(), 1)
    snap = load_articles(copy_with_meta(tmp_path, meta, changed))[1]
    assert not snap.verified and '해시와 다름' in snap.issues[0]
    assert snap.as_of is None and snap.law_name is None
    assert snap.sha256 == hashlib.sha256(changed).hexdigest()


@pytest.mark.parametrize('meta,issue', [('{깨진 json', '읽을 수 없음'), ('[1, 2]', '형식 오류')])
def test_unreadable_meta(tmp_path, meta, issue):
    snap = read_meta(copy_with_meta(tmp_path, meta), SHA)
    assert not snap.verified and issue in snap.issues[0] and snap.as_of is None


def test_invalid_date_is_not_kept(tmp_path):
    meta = {'law_name': '근로기준법', 'sha256': SHA, 'as_of': '2026.10.01', 'effective': '2025-02-23'}
    snap = read_meta(copy_with_meta(tmp_path, meta), SHA)
    assert snap.as_of is None and snap.effective == '2025-02-23'
    assert snap.issues == ("메타데이터 as_of 날짜 형식 오류: '2026.10.01'",)


def test_recorded_dates_and_source_are_preserved(tmp_path):
    meta = {'law_name': '근로기준법', 'sha256': SHA, 'as_of': '2026-10-01', 'source': '국가법령정보센터',
            'promulgated': '2025-10-01', 'effective': '2026-02-19'}
    snap = read_meta(copy_with_meta(tmp_path, meta), SHA)
    assert snap.verified
    assert (snap.source, snap.promulgated, snap.effective) == ('국가법령정보센터', '2025-10-01', '2026-02-19')


# 색인할 수 없는 데이터 식별
def broken_csv(tmp_path, edit):
    law = pd.read_csv(ARTICLES, **E)
    law = edit(law)
    path = tmp_path / 'law.csv'
    law.to_csv(path, index=False, **E)
    return path


@pytest.mark.parametrize('edit,match', [
    (lambda d: d.drop(columns='chapter'), r"열 없음 \['chapter'\]"),
    (lambda d: d.assign(deleted=d['deleted'].replace({1: 2})), 'deleted는 0 또는 1'),
    (lambda d: pd.concat([d, d.iloc[[25]]]), r"조문 번호 중복 \['제26조'\]"),
    (lambda d: d.assign(article_id=d['article_id'].replace({'제26조': '26조'})), '조문 번호 형식 오류'),
    (lambda d: d.assign(text=d['text'].where(d['article_id'] != '제26조', '  ')), r"빈 조문 \['제26조'\]"),
    (lambda d: d.assign(title=d['title'].where(d['article_id'] != '제2조')), r"빈 조문 \['제2조'\]"),
    (lambda d: d.assign(deleted=1), '색인할 조문이 없음'),
])
def test_broken_data_raises(tmp_path, edit, match):
    with pytest.raises(DataError, match=match):
        load_articles(broken_csv(tmp_path, edit))


def test_deleted_article_may_have_empty_title(tmp_path):
    # 제35조처럼 삭제된 조문은 제목이 비어 있어도 색인하지 않으므로 문제없음
    law, _ = load_articles(broken_csv(tmp_path, lambda d: d))
    assert pd.isna(law.set_index('article_id').loc['제35조', 'title'])


# 최신성·시점 안내
def snap(**kw):
    return Snapshot(data_file='a.csv', sha256='0' * 64, law_name='근로기준법', **({'as_of': '2026-10-01'} | kw))


def test_no_notes_for_ordinary_questions(metas):
    # 평가 650문항(시점 표현 없음)에 정답 조문을 줄 때, 실효 표시 조문을 빼면 안내가 붙지 않아야 함
    ev = pd.read_csv(EVAL, **E)
    for q, a in zip(ev['question'], ev['article_id']):
        notes = time_notes(q, [metas[a]], snap(), TODAY)
        assert notes == ([] if a != '제53조' else [notes[0]]), (q, notes)
        if a == '제53조':
            assert notes[0] == ('제53조 본문에 실효 표시가 있습니다: [실효: 2018.3.20. 개정법 부칙에 따라 '
                                '2022.12.31.까지 유효] [실효: 2022.12.31.까지 유효]. 표시된 부분은 현재 적용되지 않을 수 있습니다.')


def test_repo_notes_flag_articles_changed_later(loaded, metas):
    # 실제 메타데이터: 이후 개정으로 바뀐 조문이 검색되면 그 조문에만 안내, 바뀌지 않은 조문은 안내 없음
    _, s = loaded
    assert time_notes('해고 예고 기간은?', [metas['제26조'], metas['제1조']], s, TODAY) == []
    [note] = time_notes('휴게시간은?', [metas['제54조'], metas['제26조']], s, TODAY)
    assert note == ('제54조: 이 데이터 이후 공포된 개정(법률 제21784호, 2026-06-09 공포)으로 본문이 바뀌었습니다'
                    '(2026-10-20 확인). 위 본문은 개정 전 내용이고, 개정 시행 여부는 부칙에 따라 조문마다 달라 확인하지 않았습니다.')
    [note] = time_notes('근로감독관 권한', [metas['제110조']], s, TODAY)
    assert '(법률 제21533호, 2026-04-07 공포, 법률 제21784호, 2026-06-09 공포)' in note
    # 색인 130개 중 안내가 붙는 조문: 이후 개정으로 바뀐 17개 + 실효 표시 제53조
    assert sum(bool(time_notes('질문', [m], s, TODAY)) for m in metas.values()) == 18


def test_later_amendments_validation(tmp_path):
    act = {'law_number': '법률 제1호', 'promulgated': '2026-04-07', 'effective': '2026-10-08', 'articles': ['제13조']}
    base = {'law_name': '근로기준법', 'sha256': SHA, 'as_of': '2026-10-01'}
    ok = read_meta(copy_with_meta(tmp_path, base | {'later_amendments': {'checked_at': '2026-10-20', 'acts': [act]}}), SHA)
    assert ok.verified and ok.checked_at == '2026-10-20'
    assert ok.later == ({**act, 'articles': ('제13조',)},)
    for bad in [{'acts': [act]}, {'checked_at': '2026-10-20', 'acts': [act | {'articles': ['13조']}]},
                {'checked_at': '2026-10-20', 'acts': [act | {'promulgated': None}]}, {'checked_at': '2026-10-20'}]:
        snap = read_meta(copy_with_meta(tmp_path, base | {'later_amendments': bad}), SHA)
        assert snap.later == () and snap.checked_at is None
        assert snap.issues[0].startswith('메타데이터 later_amendments 형식 오류'), bad


def test_stale_uses_last_check_date():
    s = snap(checked_at='2026-12-01')
    assert time_notes('질문', [], s, date(2026, 12, 1) + timedelta(days=STALE_DAYS)) == []
    [note] = time_notes('질문', [], s, date(2026, 12, 1) + timedelta(days=STALE_DAYS + 1))
    assert note.startswith('개정 여부를 마지막으로 확인한 날(2026-12-01)이 181일 전입니다.')


def test_stale_snapshot_note():
    as_of = date(2026, 10, 1)
    assert time_notes('질문', [], snap(), as_of + timedelta(days=STALE_DAYS)) == []
    [note] = time_notes('질문', [], snap(), as_of + timedelta(days=STALE_DAYS + 1))
    assert note == f'조문 데이터 기준일(2026-10-01)이 {STALE_DAYS + 1}일 전입니다. 그 뒤 개정 여부는 확인하지 않았습니다.'


def test_unverified_or_undated_snapshot_note():
    broken = Snapshot(data_file='a.csv', sha256='0' * 64, issues=('출처 메타데이터 없음 (a.meta.json)',))
    assert time_notes('질문', [], broken, TODAY) == [
        '조문 데이터의 출처·기준일을 확인할 수 없습니다: 출처 메타데이터 없음 (a.meta.json)']
    assert time_notes('질문', [], snap(as_of=None), TODAY) == ['조문 데이터의 기준일이 기록되어 있지 않습니다.']


def test_question_with_year(metas):
    notes = time_notes('2019년에 1주 몇 시간까지 연장근로가 가능했나요?', [metas['제53조'], metas['제1조']], snap(), TODAY)
    assert notes[0] == ('질문에 특정 시점(2019년)이 있습니다. 이 데이터는 2026-10-01 기준 조문 한 가지뿐이라 '
                        '그 시점에 적용되는 조문인지 확인할 수 없습니다.')
    assert notes[1].startswith('제53조에는 2019년 이후 개정 표시(최근 2021-01-05)가 있어')
    assert not any(n.startswith('제1조') for n in notes)    # 개정 표시가 없는 조문은 언급하지 않음
    assert notes[2].startswith('제53조 본문에 실효 표시가 있습니다') and len(notes) == 3
    # 그 해보다 앞선 개정만 있으면 조문별 안내는 없음
    assert time_notes('2021년 2월 기준 연장근로', [metas['제53조']], snap(), TODAY)[1:] == [
        '제53조에는 2021년 이후 개정 표시(최근 2021-01-05)가 있어 그 시점의 내용과 다를 수 있습니다.',
        time_notes('', [metas['제53조']], snap(), TODAY)[0]]
    assert len(time_notes('2022년 이후 연장근로', [metas['제1조']], snap(), TODAY)) == 1


def test_question_mentioning_previous_law():
    [note] = time_notes('개정 전에는 어땠나요?', [], snap(), TODAY)
    assert '특정 시점(개정 전)' in note
    assert time_notes('근속 3년, 연차 15일', [], snap(), TODAY) == []   # 연도가 아닌 숫자는 무시


# 데이터 점검 명령
def test_check_command_ok(capsys):
    assert main([str(ARTICLES)]) == 0
    out = capsys.readouterr().out
    assert f'조문 132개 (색인 130개), sha256 {SHA}' in out
    assert '법령 근로기준법 법률 제21373호 · 기준일 2026-10-01 · 출처 Legalize KR · 공포일 2026-02-19 · 시행일 2026-08-20' in out
    assert '- 법률 제21784호 (2026-06-09 공포, 시행일자 2027-06-10): 제54조, 제60조, 제61조, 제110조, 제114조' in out
    assert '- 법률 제21857호 (2026-08-04 공포, 시행일자 2026-10-02): 본문 변경 없음' in out
    assert "본문 최근 개정 표시 2026-02-19 · 실효 표시가 있는 색인 조문 ['제53조']" in out
    assert '문제' not in out


def test_check_command_reports_stale_meta_and_broken_data(tmp_path, capsys):
    changed = ARTICLES.read_bytes().replace('30일'.encode(), '60일'.encode(), 1)
    assert main([str(copy_with_meta(tmp_path, {'sha256': SHA}, changed))]) == 1
    assert '문제: 데이터 파일이 메타데이터에 기록된 해시와 다름' in capsys.readouterr().out
    assert main([str(broken_csv(tmp_path, lambda d: d.drop(columns='text')))]) == 1
    assert "데이터 오류: law.csv: 열 없음 ['text']" in capsys.readouterr().out
