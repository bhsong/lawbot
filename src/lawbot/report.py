# src/lawbot/report.py  →  uv run python -m lawbot.report --tag <실행이름> [파일 지정] [--baseline <기준 실행이름>]
# results/의 평가 결과를 한 행으로 모아 results/metrics.csv에 추가하고, 기준 실행과 비교해 부작용을 경고한다 (생성 없이 바로 끝남)
#   검색: retrieval_*.csv (eval_dev) / 답변: scored_*.csv (eval_dev) / 범위 밖: oos_*.csv (eval_oos)
#   거절: refusal_*.csv (eval_refusal) / 재평가: refusal_hard_*.csv (eval_refusal --hard) / 대조군: scored_controls_*.csv (eval_dev --ids)
# 범위 밖 지표는 기준선과 비교할 수 있게 기존 82문항(OOS001–082)으로 재고, 추가 20문항은 oos_added_*로 따로 잰다
import argparse
import sys

import pandas as pd

from lawbot.paths import DATA, RESULTS
from lawbot.refusal import is_refusal, oos_verdict

E = dict(encoding='utf-8-sig')
# 지표: (설명, 좋아지는 방향, 경고 기준 = 기준 실행보다 이만큼 나빠지면)
METRICS = {
    'recall@3': ('검색 Recall@3 (650문항)', +1, 0.005),
    'total': ('답변 종합 점수', +1, 0.02),
    'total_retrieved': ('답변 종합 점수 (검색 적중 문항만)', +1, 0.02),
    'cite_ok': ('조문 인용 정확도', +1, 0.02),
    'cite_strict': ('조문 인용 정확도 (엄격)', +1, 0.02),
    'false_refusal': ('거짓 거절률 (검색 적중 문항)', -1, 0.01),
    'control_false_refusal': ('범위 안 대조군 거짓 거절률', -1, 0.05),
    'control_total': ('범위 안 대조군 종합 점수', +1, 0.05),
    'refusal_unrelated': ('무관한 조문만 제공 시 거절률', +1, None),
    'refusal_similar': ('비슷한 조문만 제공 시 거절률', +1, None),
    'hard_refusal': ('근거 없이 주장했던 81건 거절률', +1, None),
    'oos_refusal': ('범위 밖 거절률 (82문항)', +1, None),
    'oos_near_refusal': ('범위 밖 거절률 (비슷한 조문 있는 다른 노동법, near_lsa)', +1, None),
    'oos_forced': ('범위 밖 억지 적용률 (82문항)', -1, None),
    'oos_labor_forced': ('범위 밖 억지 적용률 (다른 노동법 32문항)', -1, None),
    'oos_added_forced': ('범위 밖 억지 적용률 (추가 20문항)', -1, None),
}
SOURCES = ('retrieval', 'scored', 'controls', 'oos', 'refusal', 'hard')


def read(name):
    return pd.read_csv(RESULTS / name, **E).fillna({'answer': ''}) if name else None


def refused(df):
    return df['answer'].map(is_refusal)


def compute(retrieval=None, scored=None, controls=None, oos=None, refusal=None, hard=None):
    """파일 이름(results/ 안)을 받아 지표 dict를 돌려준다. 주지 않은 파일의 지표는 빠진다"""
    m = {}
    if (r := read(retrieval)) is not None:
        m['recall@3'] = (r['rank'] <= 3).mean()
    if (s := read(scored)) is not None:
        hit = s[s['retrieved'].astype(bool)] if 'retrieved' in s else s
        m |= {'total': s['total'].mean(), 'total_retrieved': hit['total'].mean(),
              'cite_ok': s['cite_ok'].mean(), 'false_refusal': refused(hit).mean()}
        if 'cite_strict' in s:
            m['cite_strict'] = s['cite_strict'].mean()
    if (c := read(controls)) is not None:
        m |= {'control_false_refusal': refused(c).mean(), 'control_total': c['total'].mean()}
    if (o := read(oos)) is not None:
        d = pd.read_csv(DATA / 'oos_eval.csv', **E)
        o = d.merge(o[['id', 'answer']], on='id')
        o['verdict'] = [oos_verdict(a, x) for a, x in zip(o['answer'], o['refer_lsa'])]
        base, added = o[o['id'] <= 'OOS082'], o[o['id'] > 'OOS082']
        labor = base[base['category'] == 'other_labor']
        m |= {'oos_refusal': (base['verdict'] == '거절').mean(),
              'oos_near_refusal': (labor.loc[labor['near_lsa'].notna(), 'verdict'] == '거절').mean(),
              'oos_forced': (base['verdict'] == '억지적용').mean(),
              'oos_labor_forced': (labor['verdict'] == '억지적용').mean()}
        if len(added):
            m['oos_added_forced'] = (added['verdict'] == '억지적용').mean()
    if (f := read(refusal)) is not None:
        rate = refused(f).groupby(f['test']).mean()
        m |= {'refusal_unrelated': rate.get('다른 장 조문'), 'refusal_similar': rate.get('비슷한 조문')}
    if (h := read(hard)) is not None:
        m['hard_refusal'] = refused(h).mean()
    return {k: round(float(v), 4) for k, v in m.items() if v is not None}


def warnings(now, base):
    """기준 실행보다 경고 기준 이상 나빠진 지표"""
    out = []
    for k, (desc, sign, tol) in METRICS.items():
        if tol is None or pd.isna(now.get(k)) or pd.isna(base.get(k)):
            continue
        worse = (base[k] - now[k]) * sign
        if worse > tol + 1e-9:
            out.append(f'{desc}: {base[k]:.3f} → {now[k]:.3f}')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True, help='이번 실행 이름 (예: v1_4bit, v2_4bit)')
    for src in SOURCES:
        ap.add_argument(f'--{src}', help=f'results/ 안 {src} 결과 파일명')
    ap.add_argument('--baseline', help='비교할 metrics.csv의 실행 이름')
    ap.add_argument('--note', default='', help='메모 (모델·설정 변경 내용)')
    ap.add_argument('--no-save', action='store_true', help='metrics.csv에 추가하지 않고 출력만')
    ap.add_argument('--strict', action='store_true', help='부작용 경고가 있으면 종료 코드 1')
    a = ap.parse_args(argv)

    files = {src: getattr(a, src) for src in SOURCES}
    row = {'tag': a.tag} | compute(**files) | {f'file_{k}': v or '' for k, v in files.items()} | {'note': a.note}
    path = RESULTS / 'metrics.csv'
    hist = pd.read_csv(path, **E) if path.exists() else pd.DataFrame()

    shown = pd.DataFrame([row]).set_index('tag')[[k for k in METRICS if k in row]].T
    if a.baseline:
        base = hist.set_index('tag').loc[a.baseline]
        shown.insert(0, a.baseline, [base.get(k) for k in shown.index])
    shown.index = [METRICS[k][0] for k in shown.index]
    print(shown.to_string(float_format=lambda x: f'{x:.3f}'))

    warn = warnings(row, hist.set_index('tag').loc[a.baseline].to_dict()) if a.baseline else []
    if a.baseline:
        print('\n부작용 경고 없음' if not warn else '\n[부작용 경고]\n- ' + '\n- '.join(warn))
    if not a.no_save:
        if 'tag' in hist and (hist['tag'] == a.tag).any():
            sys.exit(f'metrics.csv에 이미 {a.tag}가 있습니다. 다른 --tag를 쓰세요')
        cols = ['tag', *METRICS, *(f'file_{k}' for k in SOURCES), 'note']
        out = pd.concat([hist, pd.DataFrame([row])], ignore_index=True).reindex(columns=cols)
        out.to_csv(path, index=False, **E)
        print(f'\n{path.name}에 {a.tag} 추가')
    if a.strict and warn:
        sys.exit(1)


if __name__ == '__main__':
    main()
