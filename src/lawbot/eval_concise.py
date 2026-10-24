# src/lawbot/eval_concise.py  →  uv run python -m lawbot.eval_concise --val <val.csv> --model fast --tag v4_fast
# 간결형 답변 실험 (val 전용, 기존 평가 결과 파일과 분리: results/concise_<tag>.csv)
#   full   : 모델 원문 4섹션 (기준)
#   concise: 같은 원문을 answer_format.concise로 후처리 (재생성 없음)
#   early  : concise와 같은 화면, 【설명】이 나온 시점에 생성을 멈췄다면 걸렸을 시간 (temp 0이라 원문 앞부분과 같음)
#   alt    : --alt-system의 system으로 다시 생성한 뒤 concise 후처리 (system만 바꾼 효과)
#            Fast(4섹션 학습)에 간결형 system을 주거나, Pro(간결형)에 v1 4섹션 system('v1')을 줘서 형식 효과와 모델 효과를 나눔
# val CSV 열: id, question, article_id (v4는 law도). must_include 등 키워드 열이 있으면 score_one으로 채점
import argparse
import os
import time
from pathlib import Path
import pandas as pd
from lawbot.answer_format import cited_hits, concise, sections
from lawbot.paths import DATA, RESULTS
from lawbot.refusal import is_refusal
from lawbot.score_answers import score_one
from lawbot.service import ask, load_app

# 평가 전용 데이터(README '평가 데이터 사용 제한'): 형식 선택에 쓰지 않는다
EVAL_ONLY = {DATA / f for f in ('labor_eval_keywords.csv', 'oos_eval.csv', 'scope_controls.csv', 'refusal_hard_cases.csv')}
PENALTY = ('벌칙', '벌금', '징역', '과태료')

def judge(view, raw, hits, row, finish, sec_to_view):
    """화면 글(view) 기준 지표. 인용은 원문 【근거 조문】에서 뽑은 조문 (간결형 '근거:' 줄과 같은 규칙)"""
    cite = sections(raw).get('근거 조문', '')
    used, unknown = cited_hits(cite, hits)
    gold = (row.get('law'), row['article_id']) if 'law' in row else (None, row['article_id'])
    keys = [(h.get('law') if gold[0] else None, h['article_id']) for h in used]
    retrieved = any((h.get('law') if gold[0] else None, h['article_id']) == gold for h in hits)
    d = {'retrieved': retrieved, 'refusal': is_refusal(raw), 'cite_gold': gold in keys,
         'n_cited': len(used), 'unknown_cites': len(unknown), 'cites_all_hits': len(used) == len(hits) > 1,
         'penalty_in_view': any(p in view for p in PENALTY),
         # 근거 없는 벌칙 언급 의심: 화면에 벌칙 표현이 있는데 참고 조문 중 벌칙·과태료 조문이 없음 (사람 검토용 표시)
         'penalty_unsupported': any(p in view for p in PENALTY) and not any(
             any(p in h['title'] for p in PENALTY) for h in hits),
         'truncated': finish == 'length', 'view_len': len(view), 'sec_to_view': sec_to_view}
    if isinstance(row.get('must_include'), str) and isinstance(row.get('must_not_include'), str):
        s = score_one(row, view)
        d |= {'kw_group': s['group_score'], 'kw_total': s['total'], 'must_not_ok': s['must_not_ok']}
    return d

def run(app, question, model_key):
    """(원문, 검색 조문, 측정값, 【설명】이 처음 나온 시점 초)"""
    t0, t_early = time.perf_counter(), None
    for stage, hits, raw, st in ask(app, question, model_key):
        if t_early is None and '【설명】' in raw:
            t_early = time.perf_counter() - t0
    return raw, hits, st, t_early

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--val', required=True, help='val 문항 CSV (평가 전용 test 파일은 거부)')
    ap.add_argument('--model', default='fast', help='deploy 프로필의 모델 key')
    ap.add_argument('--tag', required=True, help='results/concise_<tag>.csv')
    ap.add_argument('--n', type=int, default=0, help='앞에서 n문항만 (0이면 전체)')
    ap.add_argument('--alt-system', help="alt 조건용 system 파일, 'v1'이면 llm.SYSTEM (없으면 alt 조건 생략)")
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)
    val_path = Path(args.val).resolve()
    if val_path in EVAL_ONLY:
        raise SystemExit(f'{val_path.name}은 평가 전용입니다. 간결형 선택은 val 문항으로 하세요.')
    val = pd.read_csv(val_path, encoding='utf-8-sig', dtype=str)
    val = val.head(args.n) if args.n else val

    os.environ['LAWBOT_MAX_RESIDENT'] = '1'     # 실험은 한 모델만 올림 (Pro 실험이면 미리 올린 Fast는 내려감)
    app = load_app()
    alt = None
    if args.alt_system == 'v1':
        from lawbot.llm import SYSTEM as alt
    elif args.alt_system:
        alt = Path(args.alt_system).read_text(encoding='utf-8').strip()
    rows = []
    for _, row in val.iterrows():
        row = row.to_dict()
        base = {'id': row['id'], 'question': row['question'], 'article_id': row['article_id']}
        raw, hits, st, t_early = run(app, row['question'], args.model)
        rows.append(base | {'mode': 'full'} | judge(raw, raw, hits, row, st['finish'], st['total']) | {'raw': raw})
        rows.append(base | {'mode': 'concise'} | judge(concise(raw, hits), raw, hits, row, st['finish'], st['total'])
                    | {'raw': raw})
        cut = raw.split('【설명】')[0]                # 조기 종료했다면 받았을 원문
        rows.append(base | {'mode': 'early'} | judge(concise(cut, hits), cut, hits, row, None, t_early or st['total'])
                    | {'raw': cut})
        if alt:
            llm, _ = app.models.get(args.model)
            sys0, llm.system = llm.system, alt
            try:
                raw2, hits2, st2, _ = run(app, row['question'], args.model)
            finally:
                llm.system = sys0
            rows.append(base | {'mode': 'alt'} | judge(concise(raw2, hits2), raw2, hits2, row, st2['finish'],
                                                           st2['total']) | {'raw': raw2})
        print(row['id'], ' / '.join(f"{r['mode']}:{'거절' if r['refusal'] else int(r['cite_gold'])}"
                                    for r in rows if r['id'] == row['id']))
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'concise_{args.tag}.csv', index=False, encoding='utf-8-sig')
    hit, miss = o[o['retrieved']], o[~o['retrieved']]
    summary = pd.DataFrame({
        '정답 조문 인용률 (검색 적중)': hit.groupby('mode')['cite_gold'].mean(),
        '거짓 거절률 (검색 적중)': hit.groupby('mode')['refusal'].mean(),
        '검색 실패 시 거절률': miss.groupby('mode')['refusal'].mean(),
        '확인 안 된 인용 포함': o.groupby('mode')['unknown_cites'].apply(lambda s: (s > 0).mean()),
        '3개 모두 인용': o.groupby('mode')['cites_all_hits'].mean(),
        '벌칙 언급': o.groupby('mode')['penalty_in_view'].mean(),
        '근거 없는 벌칙 의심': o.groupby('mode')['penalty_unsupported'].mean(),
        '잘림': o.groupby('mode')['truncated'].mean(),
        '화면 글자 수': o.groupby('mode')['view_len'].mean(),
        '화면 완성까지(초, 중앙값)': o.groupby('mode')['sec_to_view'].median()})
    if 'kw_total' in o:
        summary['키워드 종합 점수'] = o.groupby('mode')['kw_total'].mean()
    print(f'\n[간결형 실험] {args.model} · val {len(val)}문항 · 검색 적중 {hit["id"].nunique()}')
    print(summary.T.round(3))

if __name__ == '__main__':
    main()
