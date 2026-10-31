# src/lawbot/build_decree_ctx.py  →  uv run python -m lawbot.build_decree_ctx [--scores fp16|mlx] [--k3]
# eval_decree(맥 Pro 답변 평가)에 넣을 참고 조문을 저장된 리랭커 점수로 고정한다 (CPU, 모델 불필요)
#   규칙은 decree_filter와 같은 코드: 법률 리랭커 top3 + 하위법령 후보 10개 중 규칙 통과 상위 k개
#     base  : 법률 3개만          same2 : 같은 법 하위법령 2개 (decree_filter same1)
#     o07   : same1+other0.7, 2개  o09   : same1+other0.9, 2개 (추천)   o09k3 : same1+other0.9, 3개 (--k3)
# 출력 (data/v4/):
#   decree_ctx_val200_rules.csv : 기존 decree_ctx_val200.csv(법률 정답 val200, 10-30 평가) + ctx_o07·ctx_o09(·ctx_o09k3)
#                                 기존 열은 그대로. 저장된 same2·base와 글자 단위 대조(어긋나면 중단)
#   decree_ctx_dq_val200.csv    : 하위법령 정답 val 444 중 질문 유형 층화 200 (시드 42). ctx_base·same2·o07·o09(·o09k3)
# --scores fp16(기본): Colab 점수. 10-30 답변(results/v4/decree_answers_pro.csv)의 base·same2와 같은 참고 조문이라 재사용됨
# --scores mlx: 맥 MLX 4비트 점수(lawbot.mlx_decree_scores). 파일 이름 끝에 _mlx (법률 쪽 대조는 건너뜀)
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from lawbot import decree_filter as F
from lawbot.paths import DATA

E = dict(encoding='utf-8-sig')
SEED, N_DQ = 42, 200
CONFIGS = {'base': (None, 0), 'same2': ('same1', 2), 'o07': ('same1+other0.7', 2), 'o09': ('same1+other0.9', 2)}


def contexts(c, art, cfgs):
    """질문마다 설정별 참고 조문 문자열 '법률1|법률2|법률3|하위법령…' (eval_decree ctx_<설정> 형식)"""
    aid, r = art.article_id.values, F.rules()
    law = ['|'.join(aid[j] for j, _ in x[0][:3]) for x in c]
    out = {}
    for name, (rule, k) in cfgs.items():
        dec = F.run(c, art, k, r[rule]) if rule else [[] for _ in c]
        out[f'ctx_{name}'] = ['|'.join([l] + [aid[j] for j in ds]) for l, ds in zip(law, dec)]
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=str(DATA / 'decree_exp'))
    ap.add_argument('--scores', choices=['fp16', 'mlx'], default='fp16')
    ap.add_argument('--k3', action='store_true', help='o09k3(같은 규칙, 3개)도 만든다')
    a = ap.parse_args()
    d, pre, suf = Path(a.dir), ('mlx_' if a.scores == 'mlx' else ''), ('_mlx' if a.scores == 'mlx' else '')
    cfgs = CONFIGS | ({'o09k3': ('same1+other0.9', 3)} if a.k3 else {})
    art = F.load(d)
    aid = art.article_id.values
    gold_law = dict(zip(art.article_id, art.parent_law))

    # 1) 법률 정답 val200: 10-30 평가와 같은 문항·열 + 새 규칙 열
    v = pd.read_csv(DATA / 'v4' / 'decree_ctx_val200.csv', dtype=str, **E)
    rp = pd.read_csv(d / 'rr_per_question.csv', **E)
    pos = {q: i for i, q in enumerate(rp.id)}
    lc = F.cands(pd.read_csv(d / f'{pre}rr_scores.csv'), len(rp), F.npz(d / 'rr_candidates.npz'))
    sub = [lc[pos[q]] for q in v.id]
    new = contexts(sub, art, cfgs)
    if a.scores == 'fp16':
        for c in ('base', 'same2'):
            bad = int((new[f'ctx_{c}'] != v[f'ctx_{c}']).sum())
            assert bad == 0, f'법률 val200 ctx_{c}가 저장값과 {bad}문항 다름'
        print('[law] base·same2가 decree_ctx_val200.csv와 200/200 일치')
    for c in [x for x in cfgs if x not in ('base', 'same2')]:
        v[f'ctx_{c}'] = new[f'ctx_{c}'].values
    out1 = DATA / 'v4' / f'decree_ctx_val200_rules{suf}.csv'
    v.to_csv(out1, index=False, **E)
    for c in cfgs:
        col = v[f'ctx_{c}'] if f'ctx_{c}' in v else new[f'ctx_{c}']
        n_dec = col.str.count(r'\|') - 2
        other = np.mean([any(gold_law.get(x) != gold_law[g] for x in s.split('|')[3:]) for s, g in zip(col, v.gold)])
        diff = int((col != v.ctx_same2).sum())
        print(f'[law] {c}: 하위법령 평균 {n_dec.mean():.2f}개 · 다른 법 붙은 문항 {other:.3f} · same2와 다른 문항 {diff}')

    # 2) 하위법령 정답 val 444 → 질문 유형 층화 200
    qa, sp = pd.read_csv(d / 'qa_decree_v4.csv', **E), pd.read_csv(d / 'split_decree_v4.csv', **E)
    dv = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    dv = dv[dv.split_emb == 'val'].reset_index(drop=True)
    assert len(dv) == 444
    dc = F.cands(pd.read_csv(d / f'{pre}dq_rr_scores.csv'), len(dv), F.npz(d / 'dq_candidates.npz'))
    idx = np.sort(np.concatenate([g.sample(n=round(len(g) * N_DQ / len(dv)), random_state=SEED).index.values
                                  for _, g in dv.groupby('question_type')]))
    s = dv.loc[idx].reset_index(drop=True)
    ctx = contexts([dc[i] for i in idx], art, cfgs)
    out = pd.DataFrame({'id': s.id, 'question': s.question, 'eval_cat': s.eval_cat, 'question_type': s.question_type,
                        'gold': s.article_id, 'answer_key': s.answer_key}).join(ctx)
    out2 = DATA / 'v4' / f'decree_ctx_dq_val200{suf}.csv'
    out.to_csv(out2, index=False, **E)
    print(f'[dq] {len(out)}문항 {out.question_type.value_counts().to_dict()}')
    seen = set()
    for c in cfgs:
        col = out[f'ctx_{c}']
        hit = np.mean([g in x.split('|') for g, x in zip(out.gold, col)])
        n_new = sum((i, x) not in seen for i, x in zip(out.id, col))
        seen |= set(zip(out.id, col))
        print(f'[dq] {c}: 정답 하위법령 포함 {hit:.3f} · 새로 생성할 답 {n_new}')
    print(f'→ {out1}\n→ {out2}')


if __name__ == '__main__':
    main()
