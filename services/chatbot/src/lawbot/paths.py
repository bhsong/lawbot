from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # services/chatbot/src/lawbot/paths.py → 레포 최상위
DATA, RESULTS = ROOT / 'data', ROOT / 'results'  # 모델 폴더(models/)는 모델 서버가 읽음 (.env의 LAWBOT_SERVE_*)

EVAL = DATA / 'labor_eval_keywords.csv'  # 근로기준법 문항 키워드 채점 기준 (eval_v4 A_lsa)

# ---- v4 웹앱 데이터 ----
# 환경 변수(LAWBOT_V4_ARTICLES 등, lawbot.deploy)로도 바꿀 수 있고, uv run python -m lawbot.check_deploy로 검사
V4_ARTICLES = DATA / 'v4' / 'articles_v4.csv'  # 7개 법률 비삭제 415조문
V4_DECREE_ARTICLES = DATA / 'v4' / 'law_articles_v4_decree.csv'  # 법률 415 + 시행령 273 + 시행규칙 124 (하위법령 첨부)
V4_QA = DATA / 'v4' / 'qa_full_v4.csv'  # v4 문항 7,618개 (Drive labor_law/v4, 법령명 붙은 정답 조문)
V4_SPLIT = DATA / 'v4' / 'split_v4.csv'  # 문항별 train/val/test (split_emb). test 2,075문항은 평가 전용
V4_SYSTEM = DATA / 'v4' / 'system_v4.txt'  # v4 4B 학습 때 쓴 system 원문 (글자 그대로, 4섹션)
V4_SYSTEM_CONCISE = DATA / 'v4' / 'system_v4_concise.txt'  # v4 Pro 간결형 system (튜닝 전 27B용 초안, 학습 system 아님)
