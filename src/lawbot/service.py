# src/lawbot/service.py  →  UI와 무관한 질의응답 흐름 (Gradio 앱이 쓰고, 이후 API 서버도 그대로 씀)
# 이 모듈은 gradio를 import하지 않는다 (tests/test_pipeline.py가 검사)
import os
from lawbot.lawdata import time_notes
from lawbot.paths import LLM_4BIT
from lawbot.refusal import cited_articles
from lawbot.retriever import Retriever
from lawbot.llm import LLM

TOP_K = 3                            # 답변 근거로 넘기는 조문 수

def load_models():
    """검색기·sLLM을 불러온다. LLM_PATH 환경 변수로 다른 sLLM을 지정"""
    retriever = Retriever()
    info, snap = retriever.index_info, retriever.snapshot
    print(f"[lawbot] 색인 {info['docs']}개 조문 · {info['data_file']} (sha256 {info['data_sha256'][:12]}) · "
          f"기준일 {snap.as_of or '확인 불가'}")
    for issue in snap.issues:
        print(f'[lawbot] 경고: {issue}')
    return retriever, LLM(os.environ.get('LLM_PATH', LLM_4BIT))

def answer_stream(retriever, llm, question, k=TOP_K):
    """(근거 조문 목록, 답변 조각 생성기). 조문은 생성 전에 정해지므로 먼저 돌려준다"""
    hits = retriever.search(question, k=k)
    return hits, (r.text for r in llm.stream(question, hits))

def source_notes(retriever, question, hits, today=None):
    """근거 조문의 시점·최신성을 이 데이터로 확인할 수 없을 때만 안내 문장 (lawdata.time_notes)"""
    return time_notes(question, [h['meta'] for h in hits], retriever.snapshot, today)

def trace_citations(answer, hits):
    """답변 【근거 조문】의 조문 번호를 검색 결과와 대조: (검색 결과에 있는 조문, 없는 조문). 거절이면 둘 다 빈 목록"""
    ids = [h['article_id'] for h in hits]
    cited = list(dict.fromkeys(cited_articles(answer)))
    return [a for a in cited if a in ids], [a for a in cited if a not in ids]
