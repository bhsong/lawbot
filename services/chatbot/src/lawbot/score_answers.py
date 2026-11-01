"""
근로기준법 챗봇 키워드 채점기

사용법:
    python score_answers.py labor_eval_keywords.csv answers.csv -o scored.csv

answers.csv: id, answer 두 열 (id는 평가 CSV의 id와 동일)
튜닝 전/후 답변 파일을 각각 채점해 결과를 비교하세요.

채점 규칙
- 비교 전 답변·키워드 모두 공백 제거(가운뎃점 ㆍ→·, 숫자 사이 쉼표 제거)
- cite_ok: 정답 조문 번호 인용 여부 (제43조가 제43조의2에 잘못 매칭되지 않도록 처리)
  거절 답변은 0 ('해당 없음 (제공된 조문: 제43조…)'의 제공 조문 목록은 인용이 아님)
- cite_strict: 【근거 조문】에 정답 조문이 있고, 헷갈리는 조문(confusable_with)이나
  번호가 비슷한 조문(제43조 ↔ 제43조의2)은 없음. 여러 조문을 함께 인용해 정답을 맞히는 경우를 따로 보는 지표
  (total에는 반영하지 않음)
- must_include: JSON [[그룹1 동의어...], [그룹2 동의어...]] → 그룹 간 AND, 그룹 내 OR
  group_score = 충족 그룹 수 / 전체 그룹 수, missing_groups에 빠진 그룹 기록
- must_not_include: JSON [표현...] → 답변 전체에서 하나라도 나오면 hit (부정문 오답 표현)
- must_not_in_conclusion: JSON [숫자...] → 헷갈리는 조문의 숫자. 【결론】 부분에서만 검사
  (결론 표시가 없으면 답변 앞 150자). 설명·참고에서 관련 조문을 언급하는 것은 감점하지 않음
- 숫자로 시작하는 키워드는 앞에 숫자가 붙은 경우를 제외(예: '3년'이 '13년'에 매칭되지 않음)
- answer_len: 공백 포함 글자 수 (튜닝 후 점수 상승이 길이 증가 때문인지 확인용)
- total = group_score × cite_ok × (must_not hit 없으면 1, 있으면 0.5)
"""

import argparse
import json
import re

import pandas as pd

from lawbot.refusal import cited_articles, is_refusal


def norm(s: str) -> str:
    s = str(s)
    s = s.replace('ㆍ', '·').replace('・', '·')
    s = re.sub(r'(?<=\d),(?=\d{3})', '', s)
    s = re.sub(r'\s+', '', s)
    return s


def term_in(term: str, text_n: str) -> bool:
    t = norm(term)
    if not t:
        return False
    if t[0].isdigit():
        return re.search(r'(?<![\d.])' + re.escape(t), text_n) is not None
    return t in text_n


def cite_ok(article_id: str, text_n: str) -> bool:
    a = norm(article_id)
    if '조의' in a:  # 제43조의2 → 정확히 일치, 뒤에 숫자 이어지면 제외(제43조의2가 제43조의20에 매칭 방지)
        return re.search(re.escape(a) + r'(?!\d)', text_n) is not None
    return re.search(r'(?<!\d)' + re.escape(a) + r'(?!의\d)', text_n) is not None


def article_family(article_id: str) -> str:
    """제43조의2 → 제43조 (번호가 비슷한 조문 묶음)"""
    return re.sub(r'의\d+$', '', article_id)


def cite_strict(article_id: str, confusable, answer: str) -> bool:
    cited = set(cited_articles(str(answer)))
    wrong = set(confusable.split(';')) if isinstance(confusable, str) and confusable else set()
    wrong |= {c for c in cited if c != article_id and article_family(c) == article_family(article_id)}
    return article_id in cited and not (cited & wrong)


def conclusion_part(answer: str) -> str:
    a = str(answer)
    m = re.search(r'【결론】(.*?)(?=【|$)', a, re.S)
    return m.group(1) if m else a[:150]


def score_one(row, answer: str) -> dict:
    t = norm(answer)
    tc = norm(conclusion_part(answer))
    groups = json.loads(row['must_include'])
    banned = json.loads(row['must_not_include'])
    hit = [any(term_in(x, t) for x in g) for g in groups]
    missing = [g for g, h in zip(groups, hit) if not h]
    banned_c = json.loads(row.get('must_not_in_conclusion', '[]') or '[]')
    bad = [b for b in banned if term_in(b, t)] + [b for b in banned_c if term_in(b, tc)]
    gscore = sum(hit) / len(groups) if groups else 1.0
    c = cite_ok(row['article_id'], t) and not is_refusal(str(answer))
    total = gscore * (1.0 if c else 0.0) * (0.5 if bad else 1.0)
    return dict(
        cite_ok=int(c),
        cite_strict=int(cite_strict(row['article_id'], row.get('confusable_with'), answer)),
        group_score=round(gscore, 4),
        groups_hit=sum(hit),
        groups_total=len(groups),
        missing_groups=json.dumps(missing, ensure_ascii=False),
        must_not_hits=json.dumps(bad, ensure_ascii=False),
        must_not_ok=int(not bad),
        answer_len=len(str(answer)),
        total=round(total, 4),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('eval_csv')
    ap.add_argument('answers_csv')
    ap.add_argument('-o', '--out', default='scored.csv')
    a = ap.parse_args()

    ev = pd.read_csv(a.eval_csv, encoding='utf-8-sig', dtype=str)
    ans = pd.read_csv(a.answers_csv, encoding='utf-8-sig', dtype=str).fillna('')
    df = ev.merge(ans[['id', 'answer']], on='id', how='left').fillna({'answer': ''})
    res = pd.DataFrame([score_one(r, r['answer']) for _, r in df.iterrows()])
    out = pd.concat([df[['id', 'question', 'question_type', 'article_id']], res, df[['answer']]], axis=1)
    out.to_csv(a.out, index=False, encoding='utf-8-sig')

    print(f'문항 수: {len(out)}')
    print(f'조문 인용 정확도: {out.cite_ok.mean():.3f} (엄격: {out.cite_strict.mean():.3f})')
    print(f'키워드 그룹 충족률: {out.group_score.mean():.3f}')
    print(f'금지 표현 없음 비율: {out.must_not_ok.mean():.3f}')
    print(f'종합 점수(total): {out.total.mean():.3f}')
    print(f'평균 답변 길이: {out.answer_len.mean():.0f}자 (중앙값 {out.answer_len.median():.0f}자)')
    print('\n질문 유형별 종합 점수:')
    print(out.groupby('question_type')[['total', 'cite_ok', 'group_score', 'answer_len']].mean().round(3).to_string())


if __name__ == '__main__':
    main()
