"""질문 평가셋 합치기·ID·train/val/test 분할 (계획: docs/plans/v5-tax-scope.md 3-1)

    uv run lawbot-qa-split --batches <배치 산출물 폴더> --articles data/v5/law_articles_v5_decree.csv \\
        --prefix T --name tax_v5 --out-dir data/v5

분할 규칙은 v4 노트북(Colab `lawbot_v4_emb_1.ipynb` 셀 ①)의 신규 질문 분할을 그대로 옮겼다. 레포의 v4 데이터로 돌리면
`data/v4/split_v4.csv`의 B_newlaw 5,016문항과 같다 (tests/test_qa_split.py).

1. 순서: 법령(조문 CSV `law`가 처음 나온 순서) → 조문(조문 CSV 행 순서) → 질문 유형(TYPES) 순으로 안정 정렬, ID를 차례로
2. test: (조문, 질문 유형)마다 1개 (`groupby.sample(1, random_state=42)`). 그룹마다 2개 이상이어야 한다
3. test를 뺀 나머지에서 임베딩 val 20% (조문별 층화), 따로 sLLM val 10% (법령별 층화), 둘 다 random_state=42
4. 라벨: test → val → train (`split_emb`, `split_sllm`). 같은 문장이 서로 다른 분할에 있으면 실패

입력 배치 폴더: `qa_<배치>.csv`·`answer_key_<배치>.csv` (질문 생성 세션 산출물, 배치 안 id는 `rid`로 남긴다)
출력: `qa_<name>.csv`, `answer_key_<name>.csv`, `split_<name>.csv` (UTF-8 BOM)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

SEED = 42
TYPES = ['situation', 'keyword', 'colloquial', 'employer', 'indirect']
QA_COLS = ['id', 'question', 'law', 'article_id', 'title', 'question_type', 'confusable_with', 'eval_cat', 'answer_key']
KEY_COLS = ['answer_key', 'law', 'article_id', 'answer', 'must_include', 'must_not_include', 'must_not_in_conclusion']
E = dict(encoding='utf-8-sig', dtype=str, keep_default_na=False)


def order(qa: pd.DataFrame, articles: pd.DataFrame) -> pd.DataFrame:
    """법령 → 조문 → 질문 유형 순 안정 정렬. 조문 CSV에 없는 법령·조문이나 모르는 유형이면 실패"""
    laws = list(dict.fromkeys(articles['law']))
    rank = {a: i for i, a in enumerate(articles['article_id'])}
    for col, known in (('law', set(laws)), ('article_id', set(rank)), ('question_type', set(TYPES))):
        bad = sorted(set(qa[col]) - known)
        if bad:
            raise ValueError(f'{col} 값이 조문 CSV·유형 목록에 없습니다: {bad[:5]}')
    keys = pd.DataFrame(
        {
            '_l': qa['law'].map(laws.index),
            '_a': qa['article_id'].map(rank),
            '_t': qa['question_type'].map(TYPES.index),
        },
        index=qa.index,
    )
    return qa.loc[keys.sort_values(['_l', '_a', '_t'], kind='stable').index].reset_index(drop=True)


def assign_ids(qa: pd.DataFrame, prefix: str, width: int = 5) -> pd.DataFrame:
    """id를 prefix + 번호(1부터)로. 원래 id는 rid 열에"""
    out = qa.copy()
    out.insert(len(out.columns), 'rid', out['id'])
    out['id'] = [f'{prefix}{i:0{width}d}' for i in range(1, len(out) + 1)]
    return out


def split(qa: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """[id, eval_cat, split_emb, split_sllm]. qa 순서 그대로 (노트북: 통합 질문 순서)"""
    groups = qa.groupby(['article_id', 'question_type']).size()
    if groups.empty or groups.min() < 2:
        raise ValueError(f'조문×유형별 질문이 2개 미만인 그룹이 있습니다: {groups[groups < 2].index.tolist()[:5]}')
    test = set(qa.groupby(['article_id', 'question_type']).sample(1, random_state=seed)['id'])
    rest = qa[~qa['id'].isin(test)]
    _, val_emb = train_test_split(rest, test_size=0.2, stratify=rest['article_id'], random_state=seed)
    _, val_sllm = train_test_split(rest, test_size=0.1, stratify=rest['law'], random_state=seed)

    def label(ids, val):
        return ['test' if i in test else 'val' if i in val else 'train' for i in ids]

    out = pd.DataFrame(
        {
            'id': qa['id'].to_numpy(),
            'eval_cat': qa['eval_cat'].to_numpy(),
            'split_emb': label(qa['id'], set(val_emb['id'])),
            'split_sllm': label(qa['id'], set(val_sllm['id'])),
        }
    )
    for col in ('split_emb', 'split_sllm'):
        spread = pd.DataFrame({'q': qa['question'].to_numpy(), 's': out[col]}).groupby('q')['s'].nunique()
        if (spread > 1).any():
            raise ValueError(
                f'{col}: 같은 질문 문장이 서로 다른 분할에 있습니다 {spread[spread > 1].index[:3].tolist()}'
            )
    return out


def read_batches(folder: Path):
    """배치 산출물 → (질문, 정답 키). 질문·정답 키 연결과 중복을 검사한다"""
    qa_files = sorted(folder.glob('qa_B*.csv'))
    key_files = sorted(folder.glob('answer_key_B*.csv'))
    if not qa_files or len(qa_files) != len(key_files):
        raise ValueError(f'{folder}: 질문 {len(qa_files)}개 · 정답 키 {len(key_files)}개 파일')
    qa = pd.concat([pd.read_csv(p, **E) for p in qa_files], ignore_index=True)
    keys = pd.concat([pd.read_csv(p, **E) for p in key_files], ignore_index=True)
    for name, df, col in (('질문', qa, 'id'), ('정답 키', keys, 'answer_key')):
        if df[col].duplicated().any():
            raise ValueError(f'{name} {col} 중복: {df.loc[df[col].duplicated(), col].head().tolist()}')
    if qa['question'].duplicated().any():
        raise ValueError(f'같은 질문 문장: {qa.loc[qa["question"].duplicated(), "question"].head(3).tolist()}')
    missing = set(qa['answer_key']) - set(keys['answer_key'])
    if missing:
        raise ValueError(f'질문이 가리키는 정답 키가 없습니다: {sorted(missing)[:5]}')
    unused = set(keys['answer_key']) - set(qa['answer_key'])
    if unused:
        raise ValueError(f'쓰이지 않는 정답 키: {sorted(unused)[:5]}')
    return qa, keys


def write(df: pd.DataFrame, path: Path):
    df.to_csv(path, index=False, encoding='utf-8-sig', lineterminator='\n')
    print(f'[저장] {path} ({len(df)}행)')


def main(argv=None):
    p = argparse.ArgumentParser(description='질문 배치 → 합친 질문·정답 키·train/val/test 분할 (v4 노트북 규칙)')
    p.add_argument('--batches', required=True, type=Path, help='qa_B*.csv·answer_key_B*.csv 폴더')
    p.add_argument(
        '--articles', required=True, type=Path, help='조문 CSV (순서 기준, 예: data/v5/law_articles_v5_decree.csv)'
    )
    p.add_argument('--prefix', required=True, help='새 질문 id 접두 (예: T → T00001)')
    p.add_argument('--name', required=True, help='출력 파일 이름 (예: tax_v5 → qa_tax_v5.csv)')
    p.add_argument('--out-dir', required=True, type=Path)
    a = p.parse_args(argv)

    qa, keys = read_batches(a.batches)
    articles = pd.read_csv(a.articles, **E)
    qa = assign_ids(order(qa[[*QA_COLS]], articles), a.prefix)
    sp = split(qa)
    keys = keys[KEY_COLS].set_index('answer_key').loc[list(dict.fromkeys(qa['answer_key']))].reset_index()
    print(f'[질문] {len(qa)} · 정답 키 {len(keys)} · 조문 {qa["article_id"].nunique()}')
    for col in ('split_emb', 'split_sllm'):
        print(f'[분할] {col} ' + ' · '.join(f'{k} {v}' for k, v in sp[col].value_counts().items()))
    a.out_dir.mkdir(parents=True, exist_ok=True)
    write(qa, a.out_dir / f'qa_{a.name}.csv')
    write(keys, a.out_dir / f'answer_key_{a.name}.csv')
    write(sp, a.out_dir / f'split_{a.name}.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
