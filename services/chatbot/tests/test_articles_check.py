# tests/test_articles_check.py  →  조문 데이터 점검 (lawbot_tools.articles_check, 계획 docs/plans/monitor-article-check.md)
# 오늘 시행 중인 법으로 만든 조문과 지금 데이터를 비교한다. 생성기(build)는 가짜로 바꿔 끼우고, 실제 원문은 -m network
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from lawbot.paths import V4_DECREE_ARTICLES
from lawbot_tools import articles_check as ac
from lawbot_tools import build_articles as ba

COLS = ba.DECREE_COLS


def rows(*items):
    """(article_id, text, deleted, effective_date[, title]) → 생성기 출력과 같은 열의 DataFrame"""
    out = []
    for aid, text, deleted, eff, *title in items:
        law, no = aid.rsplit(' ', 1)
        out.append(
            {
                'law': law,
                'article_id': aid,
                'article_no': no,
                'title': title[0] if title else '제목',
                'chapter': None,
                'deleted': deleted,
                'text': text,
                'effective_date': eff,
                'related_ids': None,
                'kind': '법률',
                'parent_law': law,
                'parent_ids': None,
                'child_ids': None,
            }
        )
    return pd.DataFrame(out, columns=COLS)


OLD = rows(
    ('시험법 제1조', '처음 목적', 0, '2020-01-01'),
    ('시험법 제2조', '처음 정의', 0, '2020-01-01'),
    ('시험법 제3조', '삭제 <2019.1.1>', 1, '2019-01-01'),
    ('시험법 제4조', '없어질 조문', 0, '2020-01-01'),
    ('시험법 제5조', '그대로', 0, '2020-01-01'),
)
NEW = rows(
    ('시험법 제1조', '바뀐 목적', 0, '2026-11-27'),
    ('시험법 제2조', '처음 정의', 0, '2020-01-01', '새 제목'),
    ('시험법 제3조', '삭제 <2019.1.1>', 1, '2019-01-02'),  # 삭제 조문의 메타만 바뀜: 답변과 무관
    ('시험법 제5조', '그대로', 0, '2020-01-05'),  # 시행일만 바뀜
    ('시험법 제6조', '새 조문', 0, '2026-11-27'),
)


def test_compare_separates_answer_changes_from_other_columns():
    d = ac.compare(OLD, NEW)
    assert [(c['article_id'], c['kind']) for c in d.answer] == [
        ('시험법 제1조', '본문'),
        ('시험법 제2조', '제목'),
        ('시험법 제4조', '없어짐'),
        ('시험법 제6조', '추가'),
    ]
    assert d.answer[0]['effective_date'] == '2026-11-27'
    assert d.meta == {'effective_date': 3}  # 양쪽에 있는 조문의 그 밖 열: 제1조·제3조(삭제 조문)·제5조
    assert ac.compare(OLD, OLD).answer == [] and ac.compare(OLD, OLD).meta == {}


def test_compare_deleted_flag():
    old = rows(('시험법 제1조', '본문', 0, '2020-01-01'), ('시험법 제2조', '삭제 <2020.1.1>', 1, '2020-01-01'))
    new = rows(('시험법 제1조', '본문', 1, '2020-01-01'), ('시험법 제2조', '되살린 본문', 0, '2026-01-01'))
    assert [(c['article_id'], c['kind']) for c in ac.compare(old, new).answer] == [
        ('시험법 제1조', '삭제됨'),
        ('시험법 제2조', '되살아남·본문'),
    ]


VERSIONS = [
    {'doc': '시험법', 'commit': 'aaaaaaaaaa', '공포번호': '200', '시행일자': '2026-11-27', 'pending': []},
    {
        'doc': '시험법',
        'commit': 'bbbbbbbbbb',
        '공포번호': '300',
        '시행일자': '2027-01-01',
        'pending': [('제7조', '2027-01-01'), ('제8조', '2026-12-08')],
    },
]


def test_pending_groups_by_document_amendment_and_date():
    assert ac.pending(VERSIONS) == [
        {'date': '2026-12-08', 'doc': '시험법', '공포번호': '300', 'articles': ['제8조']},
        {'date': '2027-01-01', 'doc': '시험법', '공포번호': '300', 'articles': ['제7조']},
    ]


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / 'HEAD').write_text('ref: refs/heads/main\n')  # 가짜 클론 (bare 저장소처럼 HEAD 파일만)
    d = tmp_path / 'v4'
    d.mkdir()
    (d / ba.DECREE_CSV).write_bytes(ba.to_csv_bytes(OLD))
    m = {
        'dataset_version': 'v4-decree',
        'reference_date': '2026-10-20',
        'articles_file': ba.DECREE_CSV,
        'source': {'name': 'Legalize KR', 'ref': 'c' * 40},
    }
    (d / 'manifest_decree.json').write_text(json.dumps(m), encoding='utf-8')
    return d


def fake_build(df, versions=VERSIONS, new=()):
    def build(repo, asof):
        files = {ba.DECREE_CSV: ba.to_csv_bytes(df), ba.LAW_CSV: b''}
        return files, versions, {'head': 'd' * 40, 'paths': {'시험법': 'kr/시험법/법률.md'}}

    def since(repo, ref, paths):
        return list(new)

    return build, since


NEW_COMMIT = {
    'doc': '시험법',
    'sha': 'e' * 40,
    'date': '2026-10-30',
    'subject': '법률: 시험법 (일부개정)',
    '공포번호': '300',
    '시행일자': '2027-01-01',
}


def test_check_same_data_exit_0_and_summary_line(data_dir, tmp_path, monkeypatch, capsys):
    build, since = fake_build(OLD, new=[NEW_COMMIT])
    monkeypatch.setattr(ac.ba, 'build', build)
    monkeypatch.setattr(ac, 'commits_since', since)
    reports = tmp_path / 'reports'
    code = ac.main(
        ['--repo', str(tmp_path), '--asof', '2026-11-02', '--data-dir', str(data_dir), '--report-dir', str(reports)]
    )
    assert code == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == f'articles same=yes changed=0 pending=2 next=2026-12-08 new=1 → {reports / "2026-11-02.md"}'
    md = (reports / '2026-11-02.md').read_text(encoding='utf-8')
    assert '시행 중인 법과 같음' in md and 'v4-decree' in md and '2026-10-20' in md
    assert '| 2026-12-08 | 시험법 | 제300호 | 제8조 |' in md  # 시행 예정
    assert '| 2026-10-30 | 시험법 | 제300호 | 2027-01-01 | eeeeeeee 법률: 시험법 (일부개정) |' in md  # 들어온 개정
    assert '다음 할 일' not in md


def test_check_different_data_exit_1_with_diff_and_next_steps(data_dir, tmp_path, monkeypatch, capsys):
    build, since = fake_build(NEW)
    monkeypatch.setattr(ac.ba, 'build', build)
    monkeypatch.setattr(ac, 'commits_since', since)
    code = ac.main(['--repo', str(tmp_path), '--asof', '2026-11-27', '--data-dir', str(data_dir)])
    out = capsys.readouterr().out
    assert code == 1
    assert out.strip().splitlines()[-1] == 'articles same=no changed=4 pending=2 next=2026-12-08 new=0'
    md = ac.render(ac.Result(**ac.check(str(tmp_path), '2026-11-27', data_dir)))
    assert '답변 근거가 다른 조문 4개' in md
    assert '| 시험법 제1조 | 본문 | 2026-11-27 |' in md
    assert '-처음 목적' in md and '+바뀐 목적' in md  # 짧은 diff
    assert 'effective_date 3' in md
    assert f'lawbot-build-articles --repo {tmp_path} --asof 2026-11-27 --out-dir data/<새 버전>' in md
    assert '--source-ref ' + 'd' * 40 in md


def test_check_unknown_source_ref(data_dir, tmp_path, monkeypatch):
    build, _ = fake_build(OLD)
    monkeypatch.setattr(ac.ba, 'build', build)
    monkeypatch.setattr(ac, 'commits_since', lambda repo, ref, paths: None)
    r = ac.check(str(tmp_path), '2026-11-02', data_dir)
    assert r['new'] is None
    assert '알 수 없음' in ac.render(ac.Result(**r)) and ac.summary_line(ac.Result(**r)).endswith('new=?')


def test_check_failure_exit_2(data_dir, tmp_path, monkeypatch, capsys):
    def broken(repo, asof):
        raise ValueError('부칙 시행일을 읽지 못했습니다')

    monkeypatch.setattr(ac.ba, 'build', broken)
    assert ac.main(['--repo', str(tmp_path), '--asof', '2026-11-02', '--data-dir', str(data_dir)]) == 2
    assert capsys.readouterr().out.strip().splitlines()[-1] == 'articles error=부칙 시행일을 읽지 못했습니다'


def test_missing_clone_exit_2(data_dir, tmp_path, capsys):
    missing = tmp_path / 'repos' / 'legalize-kr'
    assert ac.main(['--repo', str(missing), '--asof', '2026-11-02', '--data-dir', str(data_dir)]) == 2
    assert (
        capsys.readouterr().out.strip()
        == f'articles error=legalize-kr 클론이 없습니다: {missing} (git clone --filter=blob:none --no-checkout https://github.com/legalize-kr/legalize-kr)'
    )


def test_default_asof_is_today_in_korea():
    assert ac.today() == pd.Timestamp.now(tz='Asia/Seoul').date().isoformat()


# ── 합격: 실제 legalize-kr ──────────────────────────────────────────


@pytest.mark.network
def test_real_source_same_on_reference_date_and_different_after_amendment():
    """LEGALIZE_KR=<legalize-kr 클론> uv run pytest -m network -k real_source"""
    src = os.environ.get('LEGALIZE_KR')
    if not src or (not Path(src, 'HEAD').exists() and not Path(src, '.git').exists()):
        pytest.skip('LEGALIZE_KR에 legalize-kr 클론 경로를 넣으세요')
    data = V4_DECREE_ARTICLES.parent
    same = ac.Result(**ac.check(src, '2026-10-20', data))
    assert same.diff.answer == [] and same.diff.meta == {}
    later = ac.Result(**ac.check(src, '2026-11-27', data))  # 남녀고용평등법 제21700호 시행
    docs = {c['article_id'].rsplit(' ', 1)[0] for c in later.diff.answer}
    assert docs == {'남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률'} and len(later.diff.answer) == 6
