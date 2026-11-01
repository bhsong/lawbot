# tests/test_diff_answers.py  →  로컬/원격 답변 동일성 비교 (diff_answers): 같음·한 글자 다름·id 누락
import pandas as pd
import pytest

from lawbot import diff_answers as da
from lawbot.paths import RESULTS

E = dict(encoding='utf-8-sig')
ROWS = [
    {'id': 'Q1', 'refusal': False, 'answer': '【결론】예고해야 합니다.\n【근거 조문】근로기준법 제26조'},
    {'id': 'Q2', 'refusal': True, 'answer': '【결론】확인할 수 없습니다, "제공된" 조문'},  # 쉼표·따옴표·줄바꿈
    {'id': 'Q3', 'refusal': False, 'answer': ''},
]  # 빈 답변


def write(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False, **E)
    return str(path)


def run(capsys, *paths):
    with pytest.raises(SystemExit) as e:
        da.main(list(paths))
    return e.value.code, capsys.readouterr().out


def test_same_files(tmp_path, capsys):
    a, b = write(tmp_path / 'a.csv', ROWS), write(tmp_path / 'b.csv', ROWS[::-1])  # 행 순서는 상관없음
    code, out = run(capsys, a, b)
    assert code == 0 and '같음 3/3' in out and '[다름]' not in out


def test_one_char_diff(tmp_path, capsys):
    changed = [dict(r) for r in ROWS]
    changed[0]['answer'] = changed[0]['answer'].replace('제26조', '제27조')
    a, b = write(tmp_path / 'a.csv', ROWS), write(tmp_path / 'b.csv', changed)
    r = da.compare(da.load(a), da.load(b))
    pos = ROWS[0]['answer'].index('6조')
    assert r['diff'] == [('Q1', pos)] and r['same'] == ['Q2', 'Q3']
    code, out = run(capsys, a, b)
    assert code == 1 and '[다름] Q1' in out and '같음 2/3' in out


def test_prefix_and_empty():
    assert da.first_diff('abc', 'abc') is None
    assert da.first_diff('abc', 'abcd') == 3  # 한쪽이 더 김 (생성이 더 이어짐)
    assert da.first_diff('', 'a') == 0


def test_missing_id(tmp_path, capsys):
    a, b = write(tmp_path / 'a.csv', ROWS), write(tmp_path / 'b.csv', ROWS[:2] + [{'id': 'Q9', 'answer': 'x'}])
    r = da.compare(da.load(a), da.load(b))
    assert r['only_base'] == ['Q3'] and r['only_new'] == ['Q9'] and not r['diff']
    code, out = run(capsys, a, b)
    assert code == 1 and '[비교 파일에 없음] 1개: Q3' in out and '[기준 파일에 없음] 1개: Q9' in out


def test_bad_files(tmp_path):
    with pytest.raises(ValueError, match='중복 id'):
        da.load(write(tmp_path / 'dup.csv', ROWS + ROWS[:1]))
    with pytest.raises(ValueError, match='열 없음'):
        da.load(write(tmp_path / 'noans.csv', [{'id': 'Q1'}]))


@pytest.mark.parametrize('stage', ['answers', 'oos', 'refusal'])
def test_saved_results_equal_themselves(stage):
    """저장된 10-25 결과 파일을 그대로 읽어 자기 자신과 같다고 판정 (실제 파일 형식: 빈 값·줄바꿈 포함)"""
    d = da.load(RESULTS / 'v4' / f'{stage}_pro_concise_rr.csv')
    r = da.compare(d, d)
    assert len(r['same']) == len(d) == {'answers': 200, 'oos': 122, 'refusal': 200}[stage]
