from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # src/lawbot/paths.py → 레포 최상위
DATA, MODELS, RESULTS = ROOT / "data", ROOT / "models", ROOT / "results"

ARTICLES = DATA / "labor_articles_20261001.csv"
EVAL = DATA / "labor_eval_keywords.csv"
EMB = MODELS / "emb_final_v3_fp16"
LLM_4BIT = MODELS / "sllm_mlx_4bit"

# ---- v4 웹앱 배포 (Fast/Pro). 모두 미확인 자리표시: 실제 산출물 이름을 확인한 뒤 바꾼다 ----
# 환경 변수(LAWBOT_V4_ARTICLES 등, lawbot.deploy)로도 바꿀 수 있고, uv run python -m lawbot.check_deploy로 검사
V4_ARTICLES = DATA / "v4" / "articles_v4.csv"      # 7개 법률 비삭제 415조문
V4_SYSTEM = DATA / "v4" / "system_v4.txt"          # v4 4B 학습 때 쓴 system 원문 (글자 그대로, 4섹션)
V4_SYSTEM_CONCISE = DATA / "v4" / "system_v4_concise.txt"  # v4 14B QLoRA 학습 system (간결형, 글자 그대로)
V4_EMB = MODELS / "v4" / "emb_v4_fp16"             # v4 튜닝 Qwen3-Embedding-0.6B
V4_FAST = MODELS / "v4" / "sllm_4b_lora_mlx_4bit"  # Qwen3-4B + v4 LoRA 병합 → MLX 4비트
V4_PRO = MODELS / "v4" / "sllm_14b_mlx_4bit"       # Qwen3-14B MLX 4비트 (v4 QLoRA 완성 시 교체)
V4_RERANKER = MODELS / "v4" / "reranker_4b_mlx_4bit"  # Qwen3-Reranker-4B(튜닝 없음) → MLX 4비트 (LAWBOT_RERANKER_ENABLED=1일 때만)
PRO_BASE = MODELS / "qwen3_14b_mlx_4bit"           # v1 프로필에서 시험할 14B (튜닝 전)
SYSTEM_V1_CONCISE = DATA / "system_v1_concise.txt" # 튜닝 전 14B용 간결형 system (v1 4섹션 system을 줄인 초안)
