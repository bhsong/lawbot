# tests/test_report.py  →  통합 지표 리포트(lawbot.report) 검증
# 저장소의 results/는 읽기만 하고, 쓰기 테스트는 임시 폴더에서 함 (모델 불필요)
import pandas as pd
import pytest

import lawbot.report as report
from lawbot.paths import RESULTS
from lawbot.report import compute, warnings

E = dict(encoding='utf-8-sig')
ANSWER = '【결론】\n해고 예고를 해야 합니다.\n\n【근거 조문】\n근로기준법 제26조(해고의 예고)'
REFUSAL = '【결론】\n제공된 조문만으로는 질문에 대한 근거를 확인할 수 없습니다.\n\n【근거 조문】\n해당 없음'


def test_reproduces_readme_numbers():
    # README 성능 표의 맥 4비트 수치 (종합 0.812, 적중만 0.854, 범위 밖 0.671, 비슷한 조문 0.24, near 0.250)
    m = compute(scored='scored_sllm_mlx_4bit.csv', oos='oos_v1_4bit.csv', refusal='refusal_v1_4bit.csv')
    assert round(m['total'], 3) == 0.812 and round(m['total_retrieved'], 3) == 0.854
    assert m['oos_refusal'] == pytest.approx(0.671, abs=5e-4)
    assert m['refusal_similar'] == 0.24 and m['oos_near_refusal'] == 0.25
    assert m['oos_forced'] == pytest.approx(21 / 82, abs=5e-5) and m['oos_labor_forced'] == pytest.approx(13 / 32, abs=5e-5)
    h = compute(scored='scored_sllm_mlx_4bit_hybrid.csv', oos='oos_v1_4bit_hybrid.csv')
    assert round(h['total'], 3) == 0.802 and round(h['oos_refusal'], 3) == 0.683   # README 실패한 실험


def test_metrics_csv_matches_recorded_files():
    hist = pd.read_csv(RESULTS / 'metrics.csv', **E).fillna('')
    assert hist['tag'].is_unique
    for _, r in hist.iterrows():
        files = {k: r[f'file_{k}'] or None for k in report.SOURCES}
        for k, v in compute(**files).items():
            assert r[k] == pytest.approx(v), (r['tag'], k)


@pytest.mark.parametrize('change,warned', [
    ({'total': 0.79}, ['답변 종합 점수']),             # 0.022 하락
    ({'total': 0.795}, []),                            # 0.017 하락은 허용
    ({'false_refusal': 0.03}, ['거짓 거절률']),
    ({'control_false_refusal': 0.10}, ['범위 안 대조군 거짓 거절률']),   # 20문항 중 2개
    ({'recall@3': 0.93}, ['검색 Recall@3']),
    ({'total': 0.9, 'false_refusal': 0.0, 'oos_forced': 0.5}, []),       # 개선, 경고 없는 지표
])
def test_warnings(change, warned):
    base = {'total': 0.812, 'false_refusal': 0.0105, 'control_false_refusal': 0.0, 'recall@3': 0.94, 'oos_forced': 0.256}
    got = warnings(base | change, base)
    assert [w.split(' (')[0].split(':')[0] for w in got] == warned


def test_warnings_skip_missing_metric():
    assert warnings({'total': 0.5}, {'recall@3': 0.94}) == []


@pytest.fixture
def tmp_results(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'RESULTS', tmp_path)
    return tmp_path


def test_compute_new_sources(tmp_results):
    pd.DataFrame({'id': ['Q1', 'Q2', 'Q3', 'Q4'], 'rank': [1, 3, 4, 10]}).to_csv(tmp_results / 'r.csv', index=False)
    pd.DataFrame({'id': ['Q1', 'Q2'], 'answer': [ANSWER, REFUSAL], 'total': [1.0, 0.0]}).to_csv(tmp_results / 'c.csv', index=False)
    pd.DataFrame({'id': ['OOS001', 'OOS083', 'OOS084'], 'answer': [REFUSAL, ANSWER, REFUSAL]}).to_csv(tmp_results / 'o.csv', index=False)
    pd.DataFrame({'test': ['비슷한 조문'] * 2, 'id': ['Q1', 'Q2'], 'answer': [ANSWER, REFUSAL]}).to_csv(tmp_results / 'h.csv', index=False)
    m = compute(retrieval='r.csv', controls='c.csv', oos='o.csv', hard='h.csv')
    assert m['recall@3'] == 0.5
    assert m['control_false_refusal'] == 0.5 and m['control_total'] == 0.5
    assert m['oos_refusal'] == 1.0 and m['oos_added_forced'] == 0.5   # 기존 82문항과 추가 문항을 따로
    assert m['hard_refusal'] == 0.5


def test_main_saves_and_rejects_duplicate_tag(tmp_results):
    pd.DataFrame({'id': ['Q1', 'Q2'], 'rank': [1, 5]}).to_csv(tmp_results / 'r.csv', index=False)
    report.main(['--tag', 'a', '--retrieval', 'r.csv'])
    report.main(['--tag', 'b', '--retrieval', 'r.csv', '--baseline', 'a'])
    saved = pd.read_csv(tmp_results / 'metrics.csv', **E)
    assert saved['tag'].tolist() == ['a', 'b'] and saved.columns[:2].tolist() == ['tag', 'recall@3']
    with pytest.raises(SystemExit):
        report.main(['--tag', 'a', '--retrieval', 'r.csv'])
    pd.DataFrame({'id': ['Q1', 'Q2'], 'rank': [5, 5]}).to_csv(tmp_results / 'worse.csv', index=False)
    with pytest.raises(SystemExit) as e:                   # Recall@3 0.5 → 0 : --strict면 종료 코드 1
        report.main(['--tag', 'c', '--retrieval', 'worse.csv', '--baseline', 'a', '--strict', '--no-save'])
    assert e.value.code == 1
