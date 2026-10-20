# src/lawbot/service.py  →  UI와 무관한 질의응답 흐름 (Gradio 앱이 쓰고, 이후 API 서버도 그대로 씀)
# 이 모듈은 gradio를 import하지 않는다 (tests/test_pipeline.py가 검사)
import os
from lawbot.paths import LLM_4BIT
from lawbot.retriever import Retriever
from lawbot.llm import LLM

TOP_K = 3                            # 답변 근거로 넘기는 조문 수

def load_models():
    """검색기·sLLM을 불러온다. LLM_PATH 환경 변수로 다른 sLLM을 지정"""
    return Retriever(), LLM(os.environ.get('LLM_PATH', LLM_4BIT))

def answer_stream(retriever, llm, question, k=TOP_K):
    """(근거 조문 목록, 답변 조각 생성기). 조문은 생성 전에 정해지므로 먼저 돌려준다"""
    hits = retriever.search(question, k=k)
    return hits, (r.text for r in llm.stream(question, hits))
