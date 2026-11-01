# src/lawbot/decree_filter.py  →  uv run python -m lawbot.decree_filter [--dir data/decree_exp] [--scores fp16|mlx]
# 하위법령 첨부 필터 규칙 비교 (CPU만, 저장된 Colab L4 fp16 리랭커 점수 사용. 모델 불필요)
#   지표 ① 하위법령 정답 val 444: 정답 하위법령 포함률(전체·질문 유형별), 첨부 수
#   지표 ② 법률 정답 val 1,110: 첨부 수, 다른 법(정답 법률과 다른 parent_law) 하위법령 비율,
#          정답 법률 조문의 하위 조문(child_ids) 포함률, 프롬프트 토큰(법률 top3 + 첨부)
# 입력(드라이브 v4/decree_exp, v4/ → --dir 폴더에 복사):
#   dq_rr_scores.csv(qi, j, score: 하위법령 val 444 × 법률 후보 10 + 하위법령 후보 10), dq_candidates.npz(동점 순서)
#   rr_scores.csv(qi, j, score: 법률 val 1,110 × 20), rr_candidates.npz, rr_per_question.csv(id, gold), doc_tokens.csv
#   qa_decree_v4.csv, split_decree_v4.csv  + 레포 data/v4/law_articles_v4_decree.csv
#   j = 비삭제 812조문 순번(0~414 법률, 415~811 하위법령, doc_tokens.csv와 같은 순서)
# 출력: <dir>/filter_rules.csv (규칙별 지표), <dir>/filter_rules_qtype.csv (유형별 포함률)
# --scores mlx: 맥 MLX 4비트 점수(mlx_dq_rr_scores.csv·mlx_rr_scores.csv, lawbot.mlx_decree_scores)로 계산
#   → filter_rules_mlx.csv·filter_rules_qtype_mlx.csv + fp16과의 판정 일치율(점수 차이, τ 통과, 법률 1위, 첨부 집합)
# test 문항은 쓰지 않는다. τ는 fp16 점수 기준이라 맥 MLX 4비트 점수와 다를 수 있다
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from lawbot.paths import DATA

E = dict(encoding='utf-8-sig')
N_LAW = 415
TAUS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99)


def load(d):
    art = pd.read_csv(DATA / 'v4' / 'law_articles_v4_decree.csv', **E)
    art = art[art.deleted == 0].reset_index(drop=True)
    tok = pd.read_csv(d / 'doc_tokens.csv', **E)
    assert len(art) == 812 and (art.article_id.values == tok.article_id.values).all(), '조문 순서 불일치'
    art['n_tok'] = tok.n_tok.values
    return art


def cands(scores, n_q, order=None):
    """qi별 (법률 후보 [(j, s)], 하위법령 후보 [(j, s)]), 각각 점수 내림차순.
    fp16 점수 동점이 있어서, Colab(argsort kind='stable')처럼 동점은 임베딩 후보 순서를 따른다.
    order: {'T_law': (n_q, 10), 'T_dec': (n_q, 10)} (rr_candidates.npz / dq_candidates.npz). 없으면 파일 순서"""
    sc = {(int(q), int(j)): float(x) for q, j, x in scores.itertuples(index=False)}
    if order is None:
        out = [([], []) for _ in range(n_q)]
        for q, j, x in scores.itertuples(index=False):
            out[int(q)][0 if j < N_LAW else 1].append((int(j), float(x)))
        out = [(sorted(a, key=lambda c: -c[1]), sorted(b, key=lambda c: -c[1])) for a, b in out]
    else:
        out = [tuple(sorted([(int(j), sc[(q, int(j))]) for j in order[k][q]], key=lambda c: -c[1])
                     for k in ('T_law', 'T_dec')) for q in range(n_q)]
    assert all(len(a) == 10 and len(b) == 10 for a, b in out), '후보 수가 10이 아님'
    return out


def npz(path):
    if not path.exists():
        print(f'※ {path.name} 없음: 동점은 점수 파일 순서로 처리 (Colab 결과와 동점 문항이 다를 수 있음)')
        return None
    z = np.load(path)
    return {'T_law': z['T_law'], 'T_dec': z['T_dec']}


def rules():
    """이름 → f(dec, top1_law, top3_laws) → 첨부 후보 순서 리스트(점수 내림차순). 첨부 수 k는 뒤에서 자름"""
    r = {
        'none': lambda dec, l1, l3: dec,
        'same1': lambda dec, l1, l3: [c for c in dec if c[2] == l1],
        'top3laws': lambda dec, l1, l3: [c for c in dec if c[2] in l3],
    }
    for t in TAUS:
        r[f'tau{t}'] = lambda dec, l1, l3, t=t: [c for c in dec if c[1] >= t]
        r[f'same1_tau{t}'] = lambda dec, l1, l3, t=t: [c for c in dec if c[2] == l1 and c[1] >= t]  # same_t03 계열
        # 같은 법은 그대로, 다른 법은 점수 ≥ τ일 때만 (점수 순으로 섞음)
        r[f'same1+other{t}'] = lambda dec, l1, l3, t=t: [c for c in dec if c[2] == l1 or c[1] >= t]
        # 같은 법을 먼저 채우고, 남는 자리에 다른 법(점수 ≥ τ)
        r[f'same1>other{t}'] = lambda dec, l1, l3, t=t: ([c for c in dec if c[2] == l1]
                                                         + [c for c in dec if c[2] != l1 and c[1] >= t])
        r[f'top3laws+other{t}'] = lambda dec, l1, l3, t=t: [c for c in dec if c[2] in l3 or c[1] >= t]
    return r


def run(c, art, k, fn):
    """질문마다 첨부 하위법령 순번 리스트"""
    parent = art.parent_law.values
    out = []
    for law, dec in c:
        l3 = [parent[j] for j, _ in law[:3]]
        dec3 = [(j, s, parent[j]) for j, s in dec]
        out.append([j for j, _, _ in fn(dec3, l3[0], set(l3))[:k]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=str(DATA / 'decree_exp'))
    ap.add_argument('--scores', choices=['fp16', 'mlx'], default='fp16', help='Colab fp16 점수 / 맥 MLX 4비트 점수')
    args = ap.parse_args()
    d = Path(args.dir)
    pre, suf = ('mlx_', '_mlx') if args.scores == 'mlx' else ('', '')
    need = ['doc_tokens.csv', 'qa_decree_v4.csv', 'split_decree_v4.csv', 'rr_per_question.csv', 'dq_candidates.npz',
            'rr_candidates.npz', f'{pre}dq_rr_scores.csv', f'{pre}rr_scores.csv'] + (
        ['dq_rr_scores.csv', 'rr_scores.csv'] if pre else [])
    missing = [f for f in need if not (d / f).is_file()]
    if missing:
        raise SystemExit(f'{d}에 없는 파일: {", ".join(missing)}'
                         + ('\nmlx_*는 uv run python -m lawbot.mlx_decree_scores로 만든다' if pre else ''))
    art = load(d)
    aid = art.article_id.values
    idx = {a: i for i, a in enumerate(aid)}
    ntok = art.n_tok.values
    parent = art.parent_law.values
    # 정답의 하위 조문: child_ids + 그 시행령을 인용한 시행규칙 (Colab decree_1의 kids()와 같음)
    full = pd.read_csv(DATA / 'v4' / 'law_articles_v4_decree.csv', **E)
    ch = {a: set(c.split('|')) if isinstance(c, str) else set() for a, c in zip(full.article_id, full.child_ids)}
    child = [ch[a] | set().union(*(ch.get(c, set()) for c in ch[a])) for a in aid]

    # ① 하위법령 정답 val 444
    qa, sp = pd.read_csv(d / 'qa_decree_v4.csv', **E), pd.read_csv(d / 'split_decree_v4.csv', **E)
    dv = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    dv = dv[dv.split_emb == 'val'].reset_index(drop=True)
    assert len(dv) == 444
    dgold = np.array([idx[a] for a in dv.article_id])
    dc = cands(pd.read_csv(d / f'{pre}dq_rr_scores.csv'), len(dv), npz(d / 'dq_candidates.npz'))
    in10 = np.array([g in {j for j, _ in dec} for g, (_, dec) in zip(dgold, dc)])

    # ② 법률 정답 val 1,110
    lv = pd.read_csv(d / 'rr_per_question.csv', **E)
    assert len(lv) == 1110
    lgold = np.array([idx[a] for a in lv.gold])
    lc = cands(pd.read_csv(d / f'{pre}rr_scores.csv'), len(lv), npz(d / 'rr_candidates.npz'))
    law3_tok = np.array([sum(ntok[j] for j, _ in law[:3]) for law, _ in lc])
    has_child = np.array([len(child[g]) > 0 for g in lgold])
    print(f'법률 val: 법률 top3 토큰 평균 {law3_tok.mean():.0f}, 정답에 하위 연결 {has_child.mean():.3f}')

    rows, qrows = [], []
    for name, fn in rules().items():
        for k in (2, 3):
            da = run(dc, art, k, fn)
            hit = np.array([g in a for g, a in zip(dgold, da)])
            la = run(lc, art, k, fn)
            n_att = np.array([len(a) for a in la])
            n_other = np.array([sum(parent[j] != parent[g] for j in a) for g, a in zip(lgold, la)])
            child_hit = np.array([any(aid[j] in child[g] for j in a) for g, a in zip(lgold, la)])
            add_tok = np.array([sum(ntok[j] for j in a) for a in la])
            rows.append(dict(
                rule=name, k=k,
                dq_recall=hit.mean(), dq_att=np.mean([len(a) for a in da]),
                dq_fail_filter=(~hit & in10).mean(), dq_fail_emb=(~in10).mean(),
                law_att=n_att.mean(), law_att0=(n_att == 0).mean(),
                law_other_ratio=n_other.sum() / max(n_att.sum(), 1), law_other_q=(n_other > 0).mean(),
                law_child_hit=child_hit[has_child].mean(),
                law_tok=(law3_tok + add_tok).mean(), law_tok_p95=np.percentile(law3_tok + add_tok, 95)))
            qrows.append(dict(rule=name, k=k, **{t: hit[dv.question_type.values == t].mean()
                                                 for t in sorted(dv.question_type.unique())}))
    res = pd.DataFrame(rows)
    q = pd.DataFrame(qrows)
    res.to_csv(d / f'filter_rules{suf}.csv', index=False, encoding='utf-8-sig')
    q.to_csv(d / f'filter_rules_qtype{suf}.csv', index=False, encoding='utf-8-sig')
    pd.set_option('display.width', 220, 'display.max_rows', 200)
    print(f'하위법령 val: 후보 10개 안에 정답 {in10.mean():.3f}')
    print(res.round(3).to_string(index=False))
    if args.scores == 'mlx':
        compare(d, art)


def compare(d, art):
    """fp16(Colab) vs MLX 4비트: 같은 후보 쌍의 점수 차이, τ 통과 판정, 법률 1위, 추천 규칙 첨부 집합의 일치율"""
    r = rules()
    print('\n[fp16 vs MLX] 같은 후보에서의 일치율')
    for label, f, npz_name, n in (('하위법령 val 444', 'dq_rr_scores.csv', 'dq_candidates.npz', 444),
                                  ('법률 val 1,110', 'rr_scores.csv', 'rr_candidates.npz', 1110)):
        a, b = pd.read_csv(d / f), pd.read_csv(d / f'mlx_{f}')
        m = a.merge(b, on=['qi', 'j'], suffixes=('_fp16', '_mlx'), validate='one_to_one')
        assert len(m) == len(a) == len(b), f'{f}: 후보 쌍이 다름 (MLX 점수가 덜 끝났는지 확인)'
        dec = m[m.j >= N_LAW]
        diff = (m.score_fp16 - m.score_mlx).abs()
        order = npz(d / npz_name)
        cf, cm = cands(a, n, order), cands(b, n, order)
        top1 = np.mean([x[0][0][0] == y[0][0][0] for x, y in zip(cf, cm)])
        line = (f'  {label}: 점수 차이 중앙값 {diff.median():.4f} · p95 {diff.quantile(.95):.4f} · '
                f'법률 1위 일치 {top1:.3f}')
        for t in (0.7, 0.9):
            line += f' · 하위법령 점수≥{t} 판정 일치 {((dec.score_fp16 >= t) == (dec.score_mlx >= t)).mean():.3f}'
        print(line)
        for name in ('same1', 'same1+other0.7', 'same1+other0.9'):
            same = np.mean([set(x) == set(y) for x, y in zip(run(cf, art, 2, r[name]), run(cm, art, 2, r[name]))])
            print(f'    {name} k=2 첨부 집합 일치 {same:.3f}')


if __name__ == '__main__':
    main()
