# src/lawbot/smoke_decree.py  →  uv run python -m lawbot.smoke_decree [--out results/v4/smoke_decree_<모드>.json]
# 하위법령 첨부 웹앱 경로 스모크 (맥 전용, 실제 모델): 웹앱과 같은 load_app·service.ask로 예시 질문 3개를 Pro에 묻는다
#   켜짐(.env/셸 설정 그대로)과, 같은 모델로 첨부만 끈 구성(decree=None)을 나란히 돌려
#   법률 3개(순위·점수)가 같은지, 하위법령 첨부·인용, 단계별 시간, MLX 최대 메모리를 출력한다
# 결과 JSON 두 개를 --compare로 비교하면 조문·답변이 같은지 확인 (예: 배포 전후, 모델 서버 코드 변경 전후)
# 질문은 공개 예시 문장이라 결과 파일에 남긴다 (요청 로그에는 기존처럼 질문 원문 없음)
import argparse
import dataclasses
import json
import sys
from pathlib import Path

from lawbot import envfile

QUESTIONS = [
    '체불 융자 이자 몇프로 이하에요?',  # 임금채권보장법 시행령 제24조 기대
    '평균임금 계산할 때 수습 기간도 들어가나요?',  # 근로기준법 시행령 제2조 기대
    '해고 예고 없이 갑자기 해고되면 어떻게 하나요?',
]  # 법률 질문 (근로기준법 제26조)
STATS = ('search', 'rerank', 'decree_search', 'decree_rerank', 'ttft', 'total', 'prompt_tokens', 'peak_gb', 'n_decree')


def hit_row(h):
    return {k: h.get(k) for k in ('law', 'article_id', 'kind', 'score', 'rerank_score', 'emb_rank')}


def run(app, model, q):
    from lawbot import citation, service
    from lawbot.answer_format import answer_citations

    *_, (_, hits, raw, st) = service.ask(app, q, model)
    used, unknown, refused = answer_citations(raw, hits)
    return {
        'hits': [hit_row(h) for h in hits],
        'answer': raw,
        'refused': refused,
        'cited': [citation.hit_key(h) for h in used],
        'unknown': unknown,
        'stats': {k: st.get(k) for k in STATS},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='pro')
    ap.add_argument('--out', default=None, help='결과 JSON')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='두 결과 JSON의 조문·답변이 같은지 (모델 불필요)')
    a = ap.parse_args()
    if a.compare:
        x, y = (json.loads(Path(p).read_text(encoding='utf-8')) for p in a.compare)
        same = all(
            x[k][q][c]['hits'] == y[k][q][c]['hits'] and x[k][q][c]['answer'] == y[k][q][c]['answer']
            for k in ('questions',)
            for q in range(len(x[k]))
            for c in ('on', 'off')
        )
        print(f'조문·답변 동일: {same} ({x["mode"]} vs {y["mode"]})')
        raise SystemExit(0 if same else 1)
    envfile.load()
    from lawbot import service

    app = service.load_app()
    if app.decree is None or not service.uses_decrees(app, a.model):
        raise SystemExit(f'하위법령 첨부가 {a.model}에 켜져 있어야 합니다 (LAWBOT_DECREE_ENABLED=1)')
    off = dataclasses.replace(app, decree=None)  # 같은 모델·검색기로 첨부만 끔
    mode = getattr(app, 'llm_mode', 'api')
    out = {'mode': mode, 'model': a.model, 'decree': dataclasses.asdict(app.decree), 'questions': []}
    for q in QUESTIONS:
        on_r, off_r = run(app, a.model, q), run(off, a.model, q)
        n = len(off_r['hits'])
        law_same = on_r['hits'][:n] == off_r['hits']
        out['questions'].append({'question': q, 'on': on_r, 'off': off_r, 'law_hits_same': law_same})
        print(f'\n■ {q}')
        for h in on_r['hits']:
            tag = f'〔{h["kind"]}〕' if h.get('kind') else '      '
            print(
                f'  {tag} {h["law"]} {h["article_id"]} · 유사도 {h["score"]:.3f} · 리랭커 {h["rerank_score"]:.3f} '
                f'(임베딩 {h["emb_rank"]}위)'
            )
        s = on_r['stats']
        print(
            f'  법률 3개 켜짐/꺼짐 동일 {law_same} · 첨부 {s["n_decree"]}개 · 인용 {on_r["cited"]} · 확인 안 됨 {on_r["unknown"]} · '
            f'거절 {on_r["refused"]}'
        )
        print(
            f'  시간: 검색 {s["search"]:.2f} · 리랭킹 {s["rerank"]:.2f} · 하위법령 {s["decree_search"]:.2f}+{s["decree_rerank"]:.2f} · '
            f'첫 토큰 {s["ttft"]:.1f} · 완료 {s["total"]:.1f}초 (꺼짐 {off_r["stats"]["total"]:.1f}초) · '
            f'프롬프트 {s["prompt_tokens"]} (꺼짐 {off_r["stats"]["prompt_tokens"]}) · peak {s["peak_gb"]}GB'
        )
        print('  답변(켜짐): ' + on_r['answer'].strip().replace('\n', ' ')[:300])
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
        print(f'\n→ {a.out}')
    raise SystemExit(0 if all(q['law_hits_same'] for q in out['questions']) else 1)


if __name__ == '__main__':
    sys.exit(main())
