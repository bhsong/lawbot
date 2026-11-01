# src/lawbot/diff_answers.py  →  uv run python -m lawbot.diff_answers <기준.csv> <비교.csv>
# 두 평가 결과 파일(eval_v4의 answers_*·oos_*·refusal_*)의 답변 원문이 글자 단위로 같은지 id로 맞춰 비교한다
# 용도: 로컬 모드와 원격 모드(모델 서버)의 답변이 같은지 확인 → 같으면 로컬 모드 정확도 수치를 기준선으로 계속 쓴다
# 모두 같고 빠진 id가 없으면 종료 코드 0, 아니면 1. 모델을 올리지 않는다
import argparse
import sys

import pandas as pd

E = dict(encoding='utf-8-sig')
CONTEXT = 20  # 첫 차이 위치 앞뒤로 보여 줄 글자 수
SHOW_IDS = 20  # 한쪽에만 있는 id를 이만큼만 출력


def load(path):
    """id → 답변 원문. 빈 답변은 ''(NaN으로 바꾸지 않음). id 중복·열 없음은 ValueError"""
    d = pd.read_csv(path, dtype=str, keep_default_na=False, **E)
    missing = {'id', 'answer'} - set(d.columns)
    if missing:
        raise ValueError(f'{path}: 열 없음 {sorted(missing)}')
    dup = d.loc[d['id'].duplicated(), 'id'].tolist()
    if dup:
        raise ValueError(f'{path}: 중복 id {dup[:5]}')
    return dict(zip(d['id'], d['answer']))


def first_diff(a, b):
    """처음 다른 글자 위치 (0부터). 같으면 None. 한쪽이 다른 쪽의 앞부분이면 짧은 쪽 길이"""
    if a == b:
        return None
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def compare(base, new):
    """{'same': [id], 'diff': [(id, 위치)], 'only_base': [id], 'only_new': [id]}. id는 기준 파일 순서"""
    out = {
        'same': [],
        'diff': [],
        'only_base': [i for i in base if i not in new],
        'only_new': [i for i in new if i not in base],
    }
    for i, a in base.items():
        if i in new:
            pos = first_diff(a, new[i])
            if pos is None:
                out['same'].append(i)
            else:
                out['diff'].append((i, pos))
    return out


def snippet(text, pos):
    return repr(text[max(0, pos - CONTEXT) : pos + CONTEXT])


def id_list(ids):
    return ', '.join(ids[:SHOW_IDS]) + (f' 외 {len(ids) - SHOW_IDS}개' if len(ids) > SHOW_IDS else '')


def main(argv=None):
    ap = argparse.ArgumentParser(description='두 결과 CSV의 답변 원문을 id로 맞춰 글자 단위로 비교')
    ap.add_argument('base', help='기준 파일 (예: results/v4/answers_pro_concise_rr.csv)')
    ap.add_argument('new', help='비교 파일 (예: results/v4/answers_pro_concise_rr_remote.csv)')
    a = ap.parse_args(argv)
    base, new = load(a.base), load(a.new)
    r = compare(base, new)
    for i, pos in r['diff']:
        print(f'[다름] {i} · 글자 위치 {pos}(0부터)\n  기준 {snippet(base[i], pos)}\n  비교 {snippet(new[i], pos)}')
    if r['only_base']:
        print(f'[비교 파일에 없음] {len(r["only_base"])}개: {id_list(r["only_base"])}')
    if r['only_new']:
        print(f'[기준 파일에 없음] {len(r["only_new"])}개: {id_list(r["only_new"])}')
    n = len(r['same']) + len(r['diff'])
    if n == 0:
        print('[diff_answers] 겹치는 id가 없습니다: 같은 단계(answers·oos·refusal)의 파일끼리 비교하세요')
    print(
        f'[diff_answers] 같음 {len(r["same"])}/{n} · 다름 {len(r["diff"])} · '
        f'한쪽에만 있음 {len(r["only_base"]) + len(r["only_new"])}'
    )
    ok = not (r['diff'] or r['only_base'] or r['only_new'])
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
