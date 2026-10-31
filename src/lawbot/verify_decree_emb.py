# src/lawbot/verify_decree_emb.py  →  uv run python -m lawbot.verify_decree_emb   (맥 전용, 임베딩 모델만 올림)
# 하위법령 첨부를 켜도 법률 415개 임베딩이 지금과 같은지 확인한다 (docs/plans/decree-attach.md Q3)
#   A: 하위법령 끔  — 415조문 CSV, max_seq 2304 (지금 웹앱)
#   B: 하위법령 켬  — 812조문 CSV, 법률 max_seq 2304 + 하위법령 397개 max_seq 3072 (따로 인코딩)
#   → 법률 행렬 A.D와 B.D가 비트 단위로 같아야 함 (np.array_equal). 검색 결과도 val 질문 일부로 대조
# 출력: 인코딩 시간(초)과 MPS 메모리(GB, torch.mps). 수동 검증 표의 '시작 시간·메모리' 칸에 적는다
import argparse
import time
import numpy as np
import pandas as pd
from lawbot import deploy, envfile
from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES, V4_QA, V4_SPLIT

E = dict(encoding='utf-8-sig')


def mps_gb():
    import torch
    return round(torch.mps.driver_allocated_memory() / 1e9, 2)


def build(label, **kw):
    from lawbot.retriever import Retriever
    t0 = time.perf_counter()
    r = Retriever(**kw)
    sec = time.perf_counter() - t0
    print(f'[{label}] 조문 {len(r.ids)}개 (법률 {r.n_law}) · 인덱스 {sec:.1f}초 · MPS {mps_gb()}GB', flush=True)
    return r, sec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=200, help='검색 결과를 대조할 val 질문 수')
    a = ap.parse_args()
    envfile.load()
    p = deploy._profile('v4')                      # 경로·max_seq만 (하위법령 켜짐 여부와 무관)
    common = dict(model_dir=p.emb, max_seq_length=p.max_seq, doc_style='v4')
    A, sa = build('A 하위법령 끔', law_csv=V4_ARTICLES, **common)
    B, sb = build('B 하위법령 켬', law_csv=V4_DECREE_ARTICLES, decree_max_seq=deploy.DECREE_MAX_SEQ, **common)
    same_docs = A.docs == B.docs[:B.n_law]
    same = A.D.shape == B.D.shape and np.array_equal(A.D, B.D)
    print(f'법률 문서 문자열 같음 {same_docs} · 법률 임베딩 {A.D.shape} 비트 단위로 같음 {same}'
          + ('' if same else f' (최대 차이 {np.abs(A.D - B.D).max():.3g})'))
    tok = B.m.tokenizer
    n_tok = [len(tok(d)['input_ids']) for d in B.docs[B.n_law:]]
    print(f'하위법령 {len(n_tok)}개 · 최장 {max(n_tok)}토큰 · {deploy.DECREE_MAX_SEQ} 초과 {sum(t > deploy.DECREE_MAX_SEQ for t in n_tok)}개')
    qa, sp = pd.read_csv(V4_QA, **E), pd.read_csv(V4_SPLIT, **E)
    v = qa.merge(sp[['id', 'split_emb']], on='id')
    q = v[v.split_emb == 'val'].head(a.n)['question'].tolist()
    diff = sum([(h['law'], h['article_id'], h['score']) for h in A.search(x, 10)] !=
               [(h['law'], h['article_id'], h['score']) for h in B.search(x, 10)] for x in q)
    print(f'val {len(q)}문항 법률 검색 상위 10개(순위·점수) 다른 문항 {diff}')
    print(f'인덱스 시간 증가 {sb - sa:+.1f}초')
    raise SystemExit(0 if same and same_docs and diff == 0 else 1)


if __name__ == '__main__':
    main()
