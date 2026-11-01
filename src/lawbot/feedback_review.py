# src/lawbot/feedback_review.py  →  uv run python -m lawbot.feedback_review <피드백 폴더·파일>... [--out 검수.csv] [--keep 이전 검수.csv]
# 피드백 JSONL(lawbot.feedback)을 모아 사람이 검수할 CSV를 만든다 (B6 저장 → 검수 → 내보내기의 두 번째 단계)
# 1. 같은 turn_id는 마지막 줄만, 'cleared'(선택 해제)는 뺀다
# 2. 평가 데이터(v4 test 2,075·val 1,110, 하위법령 질문 val 444·test 1,880, oos·scope_controls·refusal_hard_cases·
#    labor_eval_keywords)와 겹치는 질문을 뺀다. val도 빼는 이유: 피드백으로 학습한 모델을 val로 조정·선택한다:
#    공백·문장부호를 지운 완전 일치, difflib 유사도 0.9 이상. 뺀 건수와 id는 출력한다 (질문 원문은 출력하지 않음)
# 3. 검수 CSV(엑셀·Numbers용 utf-8-sig) + 같은 이름의 .jsonl(내보내기가 읽는 전체 기록)을 쓴다. 둘 다 권한 600
#    사람이 채울 열: h_ok(맞음/틀림/모름), h_gold(정답 조문 '법령명 제N조', 여러 개면 ;), h_scope(범위 안/범위 밖), h_note
# 출력 파일에는 질문 원문이 있다: 레포 안이면 .gitignore에 걸리는 경로만 허용 (feedback_review_*는 gitignore)
import argparse
import datetime
import difflib
import json
import os
import re
from pathlib import Path

import pandas as pd

from lawbot.feedback import check_private
from lawbot.paths import DATA, EVAL, V4_QA, V4_SPLIT

DECREE_QA, DECREE_SPLIT = (
    DATA / 'decree_exp' / 'qa_decree_v4.csv',
    DATA / 'decree_exp' / 'split_decree_v4.csv',
)  # 하위법령 질문 (eval_v4.DQ_QA와 같은 파일)

E = dict(encoding='utf-8-sig')
THRESHOLD = 0.9
HUMAN = ('h_ok', 'h_gold', 'h_scope', 'h_note')
COLUMNS = (
    'feedback_id',
    'turn_id',
    'ts',
    'label',
    'reason',
    'model',
    'fmt',
    'refused',
    'question',
    'hits',
    'answer_shown',
    *HUMAN,
)
SHOW_IDS = 30  # 뺀 문항 id를 이만큼만 출력


def eval_sets():
    """(출처, id, 질문) 목록: 피드백에서 빼야 할 평가 문항"""
    qa, sp = pd.read_csv(V4_QA, **E), pd.read_csv(V4_SPLIT, **E)
    t = qa.merge(sp[['id', 'split_emb']], on='id', validate='one_to_one')
    t = t[t.eval_cat.isin(['A_lsa', 'B_newlaw'])]  # eval_v4.load_test: test 2,075·val 1,110
    out = [
        (f'v4_{s}', i, q)
        for s in ('test', 'val')
        for i, q in zip(*t.loc[t.split_emb == s, ['id', 'question']].T.values)
    ]
    qa, sp = pd.read_csv(DECREE_QA, **E), pd.read_csv(DECREE_SPLIT, **E)
    t = qa.merge(
        sp[['id', 'split_emb']], on='id', validate='one_to_one'
    )  # 하위법령 질문: τ를 고른 val 444, 평가용 test 1,880
    out += [
        (f'decree_{s}', i, q)
        for s in ('val', 'test')
        for i, q in zip(*t.loc[t.split_emb == s, ['id', 'question']].T.values)
    ]
    for name, path in (
        ('oos', DATA / 'oos_eval.csv'),
        ('scope', DATA / 'scope_controls.csv'),
        ('refusal_hard', DATA / 'refusal_hard_cases.csv'),
        ('labor_eval', EVAL),
    ):
        d = pd.read_csv(path, **E)
        out += [(name, i, q) for i, q in zip(d['id'], d['question'])]
    return out


def norm(q):
    """공백·문장부호를 지운 글 (한글·영문·숫자만)"""
    return re.sub(r'[\W_]+', '', str(q)).lower()


class Overlap:
    """평가 문항과 겹치는지: 정규화 완전 일치 → difflib 유사도"""

    def __init__(self, evals, threshold=THRESHOLD):
        self.threshold = threshold
        self.items = [(f'{src}:{i}', norm(q)) for src, i, q in evals]
        self.exact = {}
        for key, n in self.items:
            self.exact.setdefault(n, key)

    def match(self, question):
        """(평가 문항 '출처:id', 'exact' 또는 유사도) 또는 None"""
        n = norm(question)
        if n in self.exact:
            return self.exact[n], 'exact'
        best = None
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(n)
        for key, e in self.items:
            sm.set_seq1(e)
            if sm.real_quick_ratio() < self.threshold or sm.quick_ratio() < self.threshold:
                continue
            r = sm.ratio()
            if r >= self.threshold and (best is None or r > best[1]):
                best = (key, r)
        return (best[0], round(best[1], 3)) if best else None


def files(paths):
    """폴더면 feedback_*.jsonl (이름순), 파일이면 그대로"""
    out = []
    for p in map(Path, paths):
        p = p.expanduser()
        if p.is_dir():
            out += sorted(p.glob('feedback_*.jsonl'))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f'피드백 폴더·파일이 없습니다: {p}')
    return out


def load(paths):
    """(기록 목록, 깨진 줄 수). 쓰는 중에 멈춘 마지막 줄 등은 건너뛴다"""
    rows, bad = [], 0
    for f in files(paths):
        for line in f.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


def latest(rows):
    """turn_id마다 마지막 줄 (시각순, 같으면 읽은 순서). cleared는 뺀다 → (남은 기록, 해제로 뺀 turn 수)"""
    last = {}
    for r in sorted(rows, key=lambda r: r.get('ts') or ''):
        last[r['turn_id']] = r
    kept = [r for r in last.values() if r.get('label') != 'cleared']
    return kept, len(last) - len(kept)


def hit_keys(r):
    return '; '.join(f'{h.get("law") + " " if h.get("law") else ""}{h["article_id"]}' for h in r.get('hits') or [])


def review_frame(rows, keep=None):
    """검수 CSV 표. keep: 이전 검수 CSV(feedback_id로 사람이 채운 열을 이어받음)"""
    prev = {}
    if keep is not None:
        k = pd.read_csv(keep, dtype=str, keep_default_na=False, **E)
        prev = {r['feedback_id']: r for _, r in k.iterrows()}
    out = []
    for r in rows:
        p = prev.get(r['feedback_id'], {})
        out.append(
            {c: r.get(c) for c in COLUMNS if c not in HUMAN and c != 'hits'}
            | {'hits': hit_keys(r)}
            | {h: p.get(h, '') for h in HUMAN}
        )
    return pd.DataFrame(out, columns=list(COLUMNS))


def private_write(path, text):
    """권한 600으로 새로 쓴다"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    os.chmod(path, 0o600)


def companion(csv_path):
    """검수 CSV와 같은 이름의 전체 기록 JSONL"""
    return Path(csv_path).with_suffix('.jsonl')


def review(paths, out, keep=None, threshold=THRESHOLD, evals=None):
    """요약 줄 목록을 돌려주고 out(CSV)·companion(out)(JSONL)을 쓴다"""
    out = Path(out)
    check_private(out, 'feedback_review 출력', folder=False)
    check_private(companion(out), 'feedback_review 출력', folder=False)
    rows, bad = load(paths)
    kept, cleared = latest(rows)
    ov = Overlap(eval_sets() if evals is None else evals, threshold)
    final, dropped = [], []
    for r in kept:
        m = ov.match(r.get('question', ''))
        if m:
            dropped.append((r['feedback_id'], *m))
        else:
            final.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    private_write(out, '﻿' + review_frame(final, keep).to_csv(index=False))
    private_write(companion(out), ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in final))
    lines = [
        f'읽은 줄 {len(rows)} (깨진 줄 {bad}) · 답변 {len(kept) + cleared}개 · 선택 해제로 뺌 {cleared}',
        f'평가 데이터와 겹쳐 뺌 {len(dropped)} (완전 일치 {sum(d[2] == "exact" for d in dropped)}, '
        f'유사도 {threshold} 이상 {sum(d[2] != "exact" for d in dropped)})',
    ]
    lines += [f'  {fid} ↔ {key} ({how})' for fid, key, how in dropped[:SHOW_IDS]]
    if len(dropped) > SHOW_IDS:
        lines.append(f'  … 외 {len(dropped) - SHOW_IDS}건')
    d = pd.DataFrame(final, columns=['label', 'reason'])
    lines.append(
        f'검수 대상 {len(final)} · like {int((d.label == "like").sum())} · flag {int((d.label == "flag").sum())}'
        + ''.join(f' · {k} {v}' for k, v in d.loc[d.label == 'flag', 'reason'].value_counts().items())
    )
    lines.append(f'→ {out} (+ {companion(out).name})')
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='피드백 JSONL → 검수 CSV (평가 데이터와 겹치는 질문 제외)')
    ap.add_argument('paths', nargs='+', help='피드백 폴더(feedback_*.jsonl) 또는 파일. 여러 개면 함께 읽음')
    ap.add_argument('--out', default=f'feedback_review_{datetime.date.today():%Y-%m-%d}.csv')
    ap.add_argument('--keep', help='이전 검수 CSV: 같은 feedback_id의 h_* 열을 이어받음')
    ap.add_argument('--threshold', type=float, default=THRESHOLD, help='difflib 유사도 기준 (이상이면 뺌)')
    a = ap.parse_args(argv)
    for line in review(a.paths, a.out, a.keep, a.threshold):
        print(line)


if __name__ == '__main__':
    main()
