# src/lawbot/eval_decree.py  →  uv run python -m lawbot.eval_decree [--model pro] [--keys <answer_key_v4.csv>]
# 시행령·시행규칙을 참고 조문에 더했을 때 답변이 나아지는지 보는 val 실험 (맥 전용, 답변 모델만 필요)
#   base       : 법률 3개 (현재 웹앱: 임베딩 top10 → 리랭커 4B top3)
#   same2      : 법률 3개 + 법률 1위와 같은 법의 하위법령 리랭커 상위 2개
#   same_t03   : 법률 3개 + 같은 법 하위법령 중 리랭커 점수 0.3 이상, 최대 2개 (없으면 base와 같은 프롬프트)
# 참고 조문은 Colab(lawbot_v4_decree_1.ipynb, L4 fp16 임베딩·리랭커)에서 고른 것을 data/v4/decree_ctx_val200.csv로 고정해 쓴다
#   → 검색 차이 없이 '하위법령을 붙인 효과'만 본다. 임베딩·리랭커를 맥에 올리지 않음
# 문항: val 1,110 중 A_lsa·B_newlaw 100개씩 (시드 42). test 문항은 쓰지 않는다
# 결과: results/v4/decree_answers_<tag>.csv (문항×설정 한 행, 이어서 실행) → summary_decree_<tag>.csv
# 10-31 추가: --ctx로 다른 참고 조문 파일, --configs로 설정 목록 (기본은 파일의 ctx_* 열 전부)
#   uv run python -m lawbot.eval_decree --ctx data/v4/decree_ctx_val200_rules.csv --tag pro
#       → 법률 val200에 o07·o09 추가. 같은 tag라 기존 base·same2·same_t03 행은 건너뛰고, 참고 조문이 같으면 답을 재사용
#   uv run python -m lawbot.eval_decree --ctx data/v4/decree_ctx_dq_val200.csv --tag pro_dq
#       → 하위법령 정답 val200 (base·same2·o07·o09). 파일은 lawbot.build_decree_ctx가 만든다
# 모델 서버(LAWBOT_MODEL_SERVER)가 설정돼 있으면 그 서버의 답변 모델로 생성 (실험 중에는 운영 웹앱 응답이 밀림)
import argparse
import re
import time
import pandas as pd
from lawbot import envfile
from lawbot.citation import LAW_ALIASES, cite_section, refused
from lawbot.paths import DATA, RESULTS
from lawbot.score_answers import norm, score_one

E = dict(encoding='utf-8-sig')
CTX = DATA / 'v4' / 'decree_ctx_val200.csv'
ARTICLES = DATA / 'v4' / 'law_articles_v4_decree.csv'
CONFIGS = ('base', 'same2', 'same_t03')   # 기본 파일(decree_ctx_val200.csv)의 설정. 다른 파일은 ctx_* 열에서 읽음
OUT = RESULTS / 'v4'

# ---- 인용 판정: eval_v4.citations에 하위법령 이름을 더한다 ----
# eval_v4는 '근로기준법 시행령 제2조'를 '근로기준법 제2조'로 읽으므로(법령명 뒤 '시행령'을 모름) 여기서 따로 판정한다
_ALIAS = sorted(((norm(a), law) for law, al in LAW_ALIASES.items() for a in al), key=lambda x: -len(x[0]))
_TOKEN = re.compile('|'.join(re.escape(a) for a, _ in _ALIAS) + r'|시행규칙|시행령|제\d+조(?:의\d+)?')

def citations(answer):
    """【근거 조문】(없으면 답변 전체)에서 ('근로기준법 시행령', '제2조') 같은 (문서, 번호) 목록
    법령명 → 그 법률, 뒤따르는 '시행령'·'시행규칙' → 그 법률의 하위법령. 이름 없이 이어 쓴 번호는 앞 문서
    ('같은 법 시행령 제5조'도 앞에 나온 법률의 시행령으로 본다). 거절 형식('해당 없음')은 빈 목록"""
    text = cite_section(answer)
    if text.strip().startswith('해당 없음'):
        return []
    alias = dict(_ALIAS)
    base = doc = None
    out = []
    for m in _TOKEN.finditer(norm(text)):
        t = m[0]
        if t in alias:
            base = doc = alias[t]
        elif t in ('시행령', '시행규칙'):
            doc = f'{base} {t}' if base else None
        elif (doc, t) not in out:
            out.append((doc, t))
    return out

def cited_keys(answer):
    return [f'{d} {n}' if d else n for d, n in citations(answer)]

# ---- 참고 조문 ----
def article_table():
    a = pd.read_csv(ARTICLES, **E)
    a = a[a['deleted'] == 0]
    # retriever.DOC_STYLES['v4']와 같은 문서 문자열 + Retriever.search가 돌려주는 dict 형식
    return {r.article_id: {'article_id': r.article_no, 'title': r.title, 'law': r.law, 'score': 0.0,
                           'doc': f'{r.law} {r.article_no}({r.title})\n{r.text}'} for r in a.itertuples()}

def answer_model(key):
    """(LLM 또는 RemoteLLM, 모드). 웹앱과 같은 system(모델별 → 프로필)을 쓴다. 임베딩·리랭커는 올리지 않음"""
    from lawbot import deploy, security, service
    p = deploy.get_profile()
    if p.name != 'v4':
        raise SystemExit('v4 프로필에서 실행하세요 (LAWBOT_PROFILE=v4)')
    spec = {s.key: s for s in p.models}[key]
    system = service.system_text(p, spec)
    url = security.model_server_url()
    if url:
        from lawbot import remote
        from lawbot.prompts import SYSTEM
        client = remote.ModelServerClient(url, security.model_token())
        remote.wait_ready(client, 300)
        mm = remote.RemoteModelManager(p.models, client, lambda s: service.system_text(p, s) or SYSTEM)
        return mm.get(key)[0], f'remote {url}'
    from lawbot.llm import LLM
    return LLM(spec.path, prompt_style=p.doc_style, **({'system': system} if system else {})), 'local'

# ---- 채점 ----
def keyword_row(keys, row):
    if keys is None or row['answer_key'] not in keys.index:
        return None
    return keys.loc[row['answer_key']].to_dict() | {'article_id': row['gold']}

def score(row, ctx, raw, keys):
    cited = cited_keys(raw)
    ctx_set = set(ctx)
    no = refused(raw)
    decrees = [c for c in cited if ' 시행령 ' in c or ' 시행규칙 ' in c]
    d = {'refusal': no, 'cite_gold': int(row['gold'] in cited and not no),
         'cited': '|'.join(cited), 'n_cited': len(cited),
         'cites_decree': int(bool(decrees) and not no),
         'cited_outside_ctx': int(any(c not in ctx_set for c in cited)),   # 참고 조문에 없는 조문을 인용 (번호만 쓴 인용 포함)
         'answer_len': len(raw)}
    kr = keyword_row(keys, row)
    if kr is not None:
        s = score_one(kr, raw)
        d |= {'group_score': s['group_score'], 'must_not_ok': s['must_not_ok'],
              'total_kw': round(s['group_score'] * d['cite_gold'] * (1.0 if s['must_not_ok'] else 0.5), 4)}
    return d

def summarize(o, tag, configs=CONFIGS, qtype=None):
    """configs: 이번에 볼 설정 (결과 파일에 다른 설정 행이 있어도 이것만). qtype: id → 질문 유형 (하위법령 질문 세트)"""
    o = o[o.config.isin(configs)].copy()
    o['retrieved'] = [g in c.split('|') for g, c in zip(o['gold'], o['ctx'])]
    groups = [('전체', o)] + [(c, o[o.eval_cat == c]) for c in sorted(o.eval_cat.unique()) if o.eval_cat.nunique() > 1]
    if qtype is not None:
        t = o['id'].map(qtype)
        groups += [(f'유형 {q}', o[t == q]) for q in sorted(t.dropna().unique())]
    groups += [('하위법령을 붙인 문항', o[o['id'].isin(o.loc[(o.config != 'base') & (o.n_ctx > 3), 'id'])])]
    rows = []
    for gname, g in groups:
        for cfg, d in g.groupby('config', sort=False):
            hit = d[d['retrieved']]
            r = {'문항군': gname, '설정': cfg, 'n': len(d),
                 '참고 조문 수': d['n_ctx'].mean(),
                 '정답 조문이 참고 조문에 있음': d['retrieved'].mean(),
                 '정답 조문 인용 (전체)': d['cite_gold'].mean(),
                 '정답 조문 인용 (검색 적중)': hit['cite_gold'].mean(),
                 '거짓 거절 (검색 적중)': hit['refusal'].mean(),
                 '검색 실패 시 거절': d.loc[~d['retrieved'], 'refusal'].mean(),
                 '하위법령 인용': d['cites_decree'].mean(),
                 '참고 조문 밖 인용': d['cited_outside_ctx'].mean(),
                 '답변 길이(자)': d['answer_len'].mean(),
                 '프롬프트 토큰': d['prompt_tokens'].mean(),
                 '첫 토큰(초, 중앙값)': d['ttft'].median(),
                 '완료(초, 평균)': d['total'].mean()}
            if 'total_kw' in d and d['total_kw'].notna().any():
                r |= {'키워드 충족': d['group_score'].mean(), '종합 (검색 적중)': hit['total_kw'].mean()}
            rows.append(r)
    s = pd.DataFrame(rows)
    s.to_csv(OUT / f'summary_decree_{tag}.csv', index=False, **E)
    pd.set_option('display.width', 250, 'display.max_columns', None)
    print(s.round(3).to_string(index=False))
    # 같은 문항에서 base와 결과가 갈린 수 (같은 프롬프트로 재사용한 행 제외)
    p = o.pivot_table(index='id', columns='config', values=['cite_gold', 'refusal'], aggfunc='first')
    for cfg in configs[1:]:
        if ('cite_gold', cfg) in p:
            gain = int(((p['cite_gold'][cfg] == 1) & (p['cite_gold']['base'] == 0)).sum())
            loss = int(((p['cite_gold'][cfg] == 0) & (p['cite_gold']['base'] == 1)).sum())
            print(f'[{cfg} vs base] 정답 인용 얻음 {gain} · 잃음 {loss}')

def append(path, out):
    """한 행 이어 쓰기. 파일이 있으면 그 열 순서에 맞춘다 (열이 다르면 행이 밀리므로).
    기존 파일에 키워드 열이 있는데 --keys 없이 실행하면 그 열은 빈 값 → 같은 --keys로 실행하거나 --summary로 다시 채점"""
    if path.exists():
        cols = pd.read_csv(path, nrows=0, **E).columns.tolist()
        extra = [c for c in out if c not in cols]
        if extra:
            raise SystemExit(f'{path.name}에 없는 열 {extra}: 다른 tag로 실행하세요')
        pd.DataFrame([out]).reindex(columns=cols).to_csv(path, mode='a', index=False, header=False, **E)
    else:
        pd.DataFrame([out]).to_csv(path, index=False, **E)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='pro', help='답변 모델 key (deploy 프로필)')
    ap.add_argument('--tag', default=None, help='결과 파일 이름 (기본: 모델 key)')
    ap.add_argument('--keys', default=None, help='answer_key_v4.csv (Drive labor_law/v4). 주면 키워드도 채점')
    ap.add_argument('--n', type=int, default=0, help='앞에서 n문항만 (0이면 200 전체, 시험 실행용)')
    ap.add_argument('--summary', action='store_true', help='생성 없이 저장된 결과로 다시 채점·요약')
    ap.add_argument('--ctx', default=str(CTX), help='참고 조문 파일 (id, question, eval_cat, gold, answer_key, ctx_<설정>…)')
    ap.add_argument('--configs', default=None, help='쉼표로 설정 목록 (기본: 파일의 ctx_* 열 전부, 첫 번째가 비교 기준)')
    a = ap.parse_args()
    envfile.load()
    tag = a.tag or a.model
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'decree_answers_{tag}.csv'
    ctx = pd.read_csv(a.ctx, dtype=str, **E)
    ctx = ctx.head(a.n) if a.n else ctx
    configs = tuple(a.configs.split(',')) if a.configs else tuple(c[4:] for c in ctx.columns if c.startswith('ctx_'))
    missing = [c for c in configs if f'ctx_{c}' not in ctx]
    if missing:
        raise SystemExit(f'{a.ctx}에 없는 설정: {missing}')
    qtype = ctx.set_index('id')['question_type'] if 'question_type' in ctx else None
    keys = pd.read_csv(a.keys, dtype=str, **E).set_index('answer_key') if a.keys else None
    if a.summary:
        o = pd.read_csv(path, **E)
        o['answer'] = o['answer'].fillna('').astype(str)
        meta = ctx.set_index('id')
        mine = o['id'].isin(meta.index)      # 이 참고 조문 파일의 문항만 다시 채점 (같은 tag의 다른 문항 행은 그대로)
        new = pd.DataFrame([score(meta.loc[r['id']].to_dict() | {'id': r['id']}, r['ctx'].split('|'), str(r['answer']), keys)
                            for _, r in o[mine].iterrows()], index=o.index[mine])
        for c in new:
            o.loc[mine, c] = new[c]
        o.to_csv(path, index=False, **E)
        summarize(o[o['id'].isin(ctx['id'])], tag, configs, qtype)
        return

    table = article_table()
    llm, mode = answer_model(a.model)
    print(f'[eval_decree] 모델 {a.model} ({mode}) · 문항 {len(ctx)} · 설정 {", ".join(configs)} → {path}', flush=True)
    done = pd.read_csv(path, **E) if path.exists() else pd.DataFrame()
    if len(done):
        done['answer'] = done['answer'].fillna('').astype(str)
    have = set(zip(done['id'], done['config'])) if len(done) else set()
    cache = {}                      # 같은 참고 조문이면 같은 답 (탐욕 디코딩): 다시 생성하지 않고 복사
    for _, r in done.iterrows():
        cache[(r['id'], r['ctx'])] = r.to_dict()
    t0, n_gen = time.perf_counter(), 0
    for _, row in ctx.iterrows():
        row = row.to_dict()
        for cfg in configs:
            if (row['id'], cfg) in have:
                continue
            keys_ctx = row[f'ctx_{cfg}'].split('|')
            prev = cache.get((row['id'], row[f'ctx_{cfg}']))
            if prev is not None:     # base와 같은 참고 조문 (same_t03에서 붙일 하위법령이 없을 때)
                out = prev | {'config': cfg, 'reused': 1}
            else:
                t1, ttft, last, raw = time.perf_counter(), None, None, ''
                for resp in llm.stream(row['question'], [table[k] for k in keys_ctx]):
                    if ttft is None:
                        ttft = time.perf_counter() - t1
                    raw += resp.text
                    last = resp
                out = {'id': row['id'], 'eval_cat': row['eval_cat'], 'gold': row['gold'], 'config': cfg,
                       'ctx': row[f'ctx_{cfg}'], 'n_ctx': len(keys_ctx), 'reused': 0,
                       'prompt_tokens': getattr(last, 'prompt_tokens', None), 'ttft': ttft,
                       'total': time.perf_counter() - t1, 'finish': getattr(last, 'finish_reason', None)}
                out |= score(row, keys_ctx, raw, keys) | {'answer': raw}
                n_gen += 1
            cache[(row['id'], row[f'ctx_{cfg}'])] = out
            append(path, out)
        if n_gen:
            print(f"{row['id']} · 생성 {n_gen}회 · 평균 {(time.perf_counter() - t0) / n_gen:.1f}초", flush=True)
    o = pd.read_csv(path, **E)
    summarize(o[o['id'].isin(ctx['id'])], tag, configs, qtype)

if __name__ == '__main__':
    main()
