# src/lawbot/feedback_export.py  →  uv run python -m lawbot.feedback_export <검수 CSV> --out data/feedback/
# 사람이 채운 검수 CSV(lawbot.feedback_review) + 같은 이름의 .jsonl(전체 기록) → 학습·개발용 CSV (B6 세 번째 단계)
#   fb_questions.csv : 검수한 모든 질문 (h_ok 또는 h_gold가 있는 행). 새 dev 세트의 질문
#   fb_emb_pairs.csv : h_gold가 있는 행의 (질문, 정답 조문) positive. reason=retrieval이면 그때 검색 1위 조문을 hard negative로
#   fb_sft.csv       : h_ok=맞음 + label=like (system, 프롬프트, 답변 원문)
#   fb_kto.csv       : h_ok가 맞음/틀림인 행, 좋음(True)/나쁨(False) 라벨 (TRL KTOTrainer처럼 짝 없는 라벨)
# 프롬프트는 저장한 hits로 llm.build_prompt_v4를 다시 불러 만든다 (모델 불필요)
# 분할: 피드백 안에서 random_state=42로 val을 뗀다 (split 열). 평가는 계속 기존 test로만 하고, 피드백을 test에 섞지 않는다
# 출력에는 질문 원문이 있다: 레포 안이면 .gitignore에 걸리는 폴더만 허용 (data/feedback/는 gitignore). 파일 권한 600
import argparse
import json
from pathlib import Path

import pandas as pd

from lawbot import citation
from lawbot.feedback import check_private
from lawbot.feedback_review import E, companion, private_write
from lawbot.paths import V4_ARTICLES, V4_DECREE_ARTICLES

SEED = 42
ARTICLES = (V4_ARTICLES, V4_DECREE_ARTICLES)  # 법률 415 + 하위법령 첨부용 812조문 (하위법령 hit 대조·h_gold)
VAL_FRAC = 0.1
OK_VALUES = ('맞음', '틀림', '모름')
SCOPE_VALUES = ('범위 안', '범위 밖')


def read_review(path):
    """(검수 표, feedback_id → 전체 기록). 값이 틀린 칸은 행 번호와 함께 ValueError"""
    d = pd.read_csv(path, dtype=str, keep_default_na=False, **E)
    for c in ('feedback_id', 'h_ok', 'h_gold', 'h_scope', 'h_note'):
        if c not in d:
            raise ValueError(f'{path}: 열 없음 {c}')
    d = d.map(str.strip)
    errs = [f'{i + 2}행 h_ok {v!r} (맞음/틀림/모름)' for i, v in d['h_ok'].items() if v and v not in OK_VALUES]
    errs += [
        f'{i + 2}행 h_scope {v!r} (범위 안/범위 밖)' for i, v in d['h_scope'].items() if v and v not in SCOPE_VALUES
    ]
    if errs:
        raise ValueError(f'{path}: ' + '; '.join(errs[:10]))
    side = companion(path)
    if not side.is_file():
        raise FileNotFoundError(f'검수 CSV와 같은 이름의 전체 기록이 없습니다: {side} (feedback_review가 함께 씀)')
    records = {r['feedback_id']: r for r in map(json.loads, side.read_text(encoding='utf-8').splitlines()) if r}
    missing = [f for f in d['feedback_id'] if f not in records]
    if missing:
        raise ValueError(f'{side}에 없는 feedback_id: {missing[:5]}')
    return d, records


def parse_gold(value, keys):
    """'근로기준법 제26조; 최저임금법 제6조' → [('근로기준법', '제26조'), …]. 법령명이 없거나 없는 조문이면 ValueError"""
    out = []
    for item in filter(None, (s.strip() for s in value.split(';'))):
        cites = citation.parse(item)
        if len(cites) != 1 or cites[0][0] is None:
            raise ValueError(f"h_gold는 '법령명 제N조' 형식: {item!r}")
        if keys is not None and cites[0] not in keys:
            raise ValueError(f'h_gold의 조문이 조문 CSV에 없음: {item!r}')
        out.append(cites[0])
    return out


def article_docs(articles):
    """{(법령, 제N조): 검색 문서 문자열} (retriever.DOC_STYLES['v4']와 같은 문자열, 삭제 조문 제외)
    articles: 조문 CSV 하나 또는 여러 개 (앞 파일부터 합친다. 하위법령 법령명은 '근로기준법 시행령'이라 키가 겹치지 않음)"""
    if isinstance(articles, (list, tuple)):
        return {k: v for a in articles for k, v in article_docs(a).items()}
    from lawbot.retriever import DOC_STYLES

    law = pd.read_csv(articles, **E)
    law = law[law['deleted'] == 0].reset_index(drop=True)
    if 'article_no' in law:
        law['article_id'] = law['article_no']
    return dict(zip(zip(law['law'], law['article_id']), DOC_STYLES['v4'](law)))


def assign_split(ids, frac=VAL_FRAC):
    """feedback_id → 'train'/'val' (random_state=42)"""
    s = pd.Series(sorted(ids), dtype=str)
    val = set(s.sample(frac=frac, random_state=SEED)) if len(s) else set()
    return {i: 'val' if i in val else 'train' for i in s}


def export(review_csv, out, articles=ARTICLES, frac=VAL_FRAC):
    """요약 줄 목록. out 폴더에 CSV 4개"""
    from lawbot.llm import build_prompt_v4

    out = Path(out)
    check_private(out, '--out')
    d, records = read_review(review_csv)
    docs = article_docs(articles)
    d = d[(d['h_ok'] != '') | (d['h_gold'] != '')]
    golds = {
        f: parse_gold(g, set(docs)) for f, g in zip(d['feedback_id'], d['h_gold'])
    }  # 틀린 칸이 있으면 쓰기 전에 멈춤
    skipped = [f for f in d['feedback_id'] if records[f].get('profile') != 'v4']
    d = d[~d['feedback_id'].isin(skipped)]  # v1 기록은 형식이 달라 내보내지 않음
    split = assign_split(d['feedback_id'], frac)
    stale = 0
    qs, pairs, sft, kto = [], [], [], []
    for _, row in d.iterrows():
        f = row['feedback_id']
        r, sp = records[f], split[f]
        hits = r['hits']
        stale += any(docs.get((h.get('law'), h['article_id'])) != h['doc'] for h in hits)
        qs.append(
            {'feedback_id': f, 'split': sp, 'question': r['question'], 'label': r['label'], 'reason': r['reason']}
            | {c: row[c] for c in ('h_ok', 'h_gold', 'h_scope', 'h_note')}
        )
        gold = golds[f]
        if gold:
            top = hits[0] if hits and r.get('reason') == 'retrieval' else None
            neg = top['doc'] if top and (top.get('law'), top['article_id']) not in gold else ''
            pairs += [
                {
                    'feedback_id': f,
                    'split': sp,
                    'question': r['question'],
                    'positive': docs[g],
                    'negative': neg,
                    'gold': citation.key(*g),
                }
                for g in gold
            ]
        if row['h_ok'] in ('맞음', '틀림'):
            ex = {
                'feedback_id': f,
                'split': sp,
                'model': r['model'],
                'fmt': r['fmt'],
                'system': r['system'],
                'prompt': build_prompt_v4(hits, r['question']),
                'completion': r['answer_raw'],
            }
            kto.append(ex | {'label': row['h_ok'] == '맞음'})
            if row['h_ok'] == '맞음' and r['label'] == 'like':
                sft.append(ex)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (('fb_questions', qs), ('fb_emb_pairs', pairs), ('fb_sft', sft), ('fb_kto', kto)):
        private_write(out / f'{name}.csv', '﻿' + pd.DataFrame(rows).to_csv(index=False))
    lines = [
        f'검수한 질문 {len(qs)} (val {sum(s == "val" for s in split.values())}) · 임베딩 쌍 {len(pairs)} '
        f'(hard negative {sum(bool(p["negative"]) for p in pairs)}) · SFT {len(sft)} · KTO {len(kto)} → {out}'
    ]
    if skipped:
        lines.append(f'v4 프로필이 아니어서 뺌 {len(skipped)}: {skipped[:5]}')
    no_gold = [f for f, g in golds.items() if not g and records[f].get('reason') == 'retrieval' and f not in skipped]
    if no_gold:  # 검색 오류는 정답 조문이 있어야 임베딩 쌍이 된다
        lines.append(f'⚠ 검색 오류인데 h_gold가 빈 행 {len(no_gold)}: {no_gold[:5]}')
    if stale:
        lines.append(
            f'⚠ 저장한 참고 조문이 지금 조문 CSV와 다른 기록 {stale}건 (법령 데이터가 바뀜: dataset_version 확인)'
        )
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='검수 CSV → 학습·개발용 CSV (fb_questions·fb_emb_pairs·fb_sft·fb_kto)')
    ap.add_argument('review', help='사람이 채운 feedback_review_<날짜>.csv (같은 이름의 .jsonl이 옆에 있어야 함)')
    ap.add_argument('--out', default='data/feedback/')
    ap.add_argument(
        '--articles',
        nargs='+',
        default=[str(a) for a in ARTICLES],
        help='정답 조문·참고 조문 본문을 찾을 조문 CSV (기본: v4 법률 + 하위법령 첨부용)',
    )
    ap.add_argument('--val-frac', type=float, default=VAL_FRAC)
    a = ap.parse_args(argv)
    for line in export(a.review, a.out, a.articles, a.val_frac):
        print(line)


if __name__ == '__main__':
    main()
