# src/lawbot/mlx_decree_scores.py  →  uv run python -m lawbot.mlx_decree_scores [--set dq|law|all] [--limit 0]
# 하위법령 필터 규칙을 맥 MLX 4비트 리랭커 점수로 다시 재기 위한 점수 파일을 만든다 (맥 전용, 리랭커 모델만 올림)
#   후보: Colab과 같은 임베딩 후보 (dq_candidates.npz / rr_candidates.npz의 T_law 10 + T_dec 10) → 리랭커 차이만 본다
#   문서: 앱과 같은 retriever.DOC_STYLES['v4'] 문자열 (Colab DOCS와 812조문 모두 글자 단위로 같음, 10-31 확인)
#   리랭커: .env의 LAWBOT_RERANKER_MODEL_PATH (lawbot.reranker.Reranker, 웹앱과 같은 채점)
# 출력(--dir, 기본 data/decree_exp): mlx_dq_rr_scores.csv (444×20), mlx_rr_scores.csv (1,110×20)
#   열 qi, j, score — Colab dq_rr_scores.csv·rr_scores.csv와 같은 형식. 문항 단위로 이어 쓰므로 멈춰도 같은 명령으로 이어서 실행
# 끝나면: uv run python -m lawbot.decree_filter --scores mlx  (MLX 점수로 규칙 비교 + fp16과 판정 일치율)
# 예상 시간(맥미니 M6, 쌍당 약 0.2초): dq 약 30분, law 약 75분
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from lawbot import deploy, envfile
from lawbot.paths import DATA
from lawbot.retriever import DOC_STYLES

E = dict(encoding='utf-8-sig')
NEED = ('doc_tokens.csv', 'qa_decree_v4.csv', 'split_decree_v4.csv', 'dq_candidates.npz', 'rr_candidates.npz')
SETS = {  # 이름: (후보 npz, 출력 파일, 문항 수)
    'dq': ('dq_candidates.npz', 'mlx_dq_rr_scores.csv', 444),
    'law': ('rr_candidates.npz', 'mlx_rr_scores.csv', 1110),
}


def questions(name, d):
    """Colab 노트북과 같은 문항 순서 (qi = 행 번호)"""
    if name == 'dq':
        qa, sp = pd.read_csv(d / 'qa_decree_v4.csv', **E), pd.read_csv(d / 'split_decree_v4.csv', **E)
    else:
        qa, sp = pd.read_csv(DATA / 'v4' / 'qa_full_v4.csv', **E), pd.read_csv(DATA / 'v4' / 'split_v4.csv', **E)
    v = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    v = v[v.split_emb == 'val'].reset_index(drop=True)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=str(DATA / 'decree_exp'))
    ap.add_argument('--set', choices=['dq', 'law', 'all'], default='all')
    ap.add_argument('--limit', type=int, default=0, help='앞에서 n문항만 (시험 실행용, 0이면 전체)')
    args = ap.parse_args()
    envfile.load()
    d = Path(args.dir)
    missing = [f for f in NEED if not (d / f).is_file()]
    if missing:
        raise SystemExit(
            f'{d}에 없는 파일: {", ".join(missing)}\n'
            "드라이브 labor_law/v4/decree_exp/·labor_law/v4/에서 복사하세요 (가이드 '실행 방법' 절)"
        )
    art = pd.read_csv(DATA / 'v4' / 'law_articles_v4_decree.csv', **E)
    art = art[art.deleted == 0].reset_index(drop=True)
    tok = pd.read_csv(d / 'doc_tokens.csv', **E)
    assert len(art) == 812 and (art.article_id.values == tok.article_id.values).all(), '조문 순서 불일치'
    art['article_id'] = art['article_no']  # retriever와 같은 규칙
    docs = DOC_STYLES['v4'](art).tolist()

    from lawbot.reranker import Reranker  # mlx는 여기서 import (위 검사는 모델 없이)

    rr_cfg = deploy.reranker(deploy.get_profile('v4'))
    if rr_cfg is None:
        raise SystemExit('.env에서 LAWBOT_RERANKER_ENABLED=1, LAWBOT_RERANKER_MODEL_PATH를 확인하세요')
    t0 = time.perf_counter()
    rr = Reranker(rr_cfg.path)
    print(f'[mlx] 리랭커 {rr_cfg.path.name} 불러오기 {time.perf_counter() - t0:.1f}초', flush=True)

    for name in ['dq', 'law'] if args.set == 'all' else [args.set]:
        npz_name, out_name, n_q = SETS[name]
        z = np.load(d / npz_name)
        T = np.concatenate([z['T_law'], z['T_dec']], 1)  # 법률 10 + 하위법령 10
        v = questions(name, d)
        assert len(v) == n_q == len(T), (name, len(v), len(T))
        out = d / out_name
        done = set(pd.read_csv(out)['qi']) if out.exists() else set()
        todo = [i for i in range(n_q if not args.limit else min(args.limit, n_q)) if i not in done]
        print(f'[mlx {name}] {len(done)}/{n_q} 완료 → 남은 {len(todo)}', flush=True)
        t0 = time.perf_counter()
        for k, qi in enumerate(todo, 1):
            s = rr.score(v.question[qi], [docs[j] for j in T[qi]])
            pd.DataFrame({'qi': qi, 'j': T[qi], 'score': s}).to_csv(out, mode='a', index=False, header=not out.exists())
            if k % 20 == 0 or k == len(todo):
                el = time.perf_counter() - t0
                print(
                    f'[mlx {name}] {k}/{len(todo)} · 문항당 {el / k:.1f}초 · 남은 약 {el / k * (len(todo) - k) / 60:.0f}분',
                    flush=True,
                )
    print('[mlx] 완료 → uv run python -m lawbot.decree_filter --scores mlx')


if __name__ == '__main__':
    main()
