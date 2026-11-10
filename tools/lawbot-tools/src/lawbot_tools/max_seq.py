"""조문 CSV의 임베딩 최대 토큰 (계획: docs/archives/v5-tax-scope.md 3-2)

    uv run lawbot-max-seq data/v5/law_articles_v5_decree.csv --tokenizer models/v5/emb_v5b_fp16/tokenizer.json --training-max-seq 5120

v4 노트북 규칙: 검색 문서(retriever.doc_text, '법령명 제N조(제목)\\n본문')의 최장 토큰 수를 256 단위로 올린다. 법률과
하위법령(시행령·시행규칙)을 따로 센다 (웹앱이 따로 인코딩). 출력은 .env에 넣을 두 줄 (LAWBOT_MAX_SEQ, LAWBOT_DECREE_MAX_SEQ).
`--training-max-seq`를 주면 권장값은 학습 상한까지만 올리고, 초과 조문과 잘리는 비율을 출력한다.
최장 길이와 `--counts`는 전체 문서 토큰 수다. 토크나이저 파일은 임베딩 모델 폴더의 tokenizer.json
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from lawbot.decree import LAW_KIND
from lawbot.retriever import doc_text

STEP = 256
ENV = {'법률': 'LAWBOT_MAX_SEQ', '하위법령': 'LAWBOT_DECREE_MAX_SEQ'}


def round_up(n: int) -> int:
    return math.ceil(n / STEP) * STEP


def _docs(df: pd.DataFrame):
    """(구분, 조문 키, 검색 문서) 비삭제 조문만. 구분: 법률 / 하위법령"""
    live = df[df['deleted'].astype(int) == 0].copy()
    keys = live['law'] + ' ' + live['article_no']
    live['article_id'] = live['article_no']  # 웹앱 검색기와 같은 문서 문자열
    kinds = live['kind'] if 'kind' in live else pd.Series(LAW_KIND, index=live.index)
    groups = ['법률' if k == LAW_KIND else '하위법령' for k in kinds]
    return list(zip(groups, keys, doc_text(live)))


def longest(df: pd.DataFrame, count: Callable[[str], int]) -> dict[str, tuple[str, int]]:
    """구분 → (최장 조문 키, 토큰 수)"""
    out: dict[str, tuple[str, int]] = {}
    for group, key, doc in _docs(df):
        n = count(doc)
        if group not in out or n > out[group][1]:
            out[group] = (key, n)
    return out


def counts(df: pd.DataFrame, count: Callable[[str], int]) -> pd.DataFrame:
    """[article(법령 제N조), group(법률·하위법령), tokens] 비삭제 조문마다 (평가 요약의 길이 구간용)"""
    rows = [{'article': key, 'group': group, 'tokens': count(doc)} for group, key, doc in _docs(df)]
    return pd.DataFrame(rows, columns=['article', 'group', 'tokens'])


def settings(df: pd.DataFrame, count: Callable[[str], int]) -> dict[str, int]:
    """{'LAWBOT_MAX_SEQ': …, 'LAWBOT_DECREE_MAX_SEQ': …} (하위법령이 없으면 앞의 것만)"""
    return {ENV[g]: round_up(n) for g, (_, n) in longest(df, count).items()}


def main(argv=None):
    p = argparse.ArgumentParser(description='조문 CSV → 임베딩 최대 토큰 (.env 두 줄, v4 노트북 규칙)')
    p.add_argument('csv', type=Path)
    p.add_argument('--tokenizer', required=True, type=Path, help='임베딩 모델의 tokenizer.json')
    p.add_argument('--counts', type=Path, help='조문별 토큰 수 CSV도 쓴다 (평가 요약의 길이 구간용)')
    p.add_argument('--training-max-seq', type=int, help='임베딩 모델 학습 상한. 권장값을 제한하고 잘리는 조문을 출력')
    a = p.parse_args(argv)
    cap = a.training_max_seq
    if cap is not None and cap < 1:
        p.error('--training-max-seq는 양의 정수여야 합니다')
    from tokenizers import Tokenizer  # 모델 서버 의존성으로 설치됨. 이 명령에서만 쓴다

    tok = Tokenizer.from_file(str(a.tokenizer))
    tok.no_truncation()  # 파일에 자르기 길이가 들어 있으면 최장 길이를 못 잰다
    tok.no_padding()

    def count(text: str) -> int:
        return len(tok.encode(text).ids)

    df = pd.read_csv(a.csv, encoding='utf-8-sig', dtype={'article_no': str})
    for group, (key, n) in longest(df, count).items():
        print(f'# {group} 최장: {key} {n:,}토큰')
    for name, value in settings(df, count).items():
        print(f'{name}={value if cap is None else min(value, cap)}')
    if cap is not None or a.counts:
        measured = counts(df, count)
        if cap is not None:
            for article, group, n in measured.loc[measured['tokens'] > cap].itertuples(index=False, name=None):
                clipped = n - cap
                print(f'# {group} 잘림: {article} {n:,}토큰 중 {clipped:,}토큰 ({clipped / n:.2%})')
        if a.counts:
            measured.to_csv(a.counts, index=False, encoding='utf-8-sig', lineterminator='\n')
            print(f'# 조문별 토큰 수: {a.counts}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
