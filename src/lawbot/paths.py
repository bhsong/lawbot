from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # src/lawbot/paths.py → 레포 최상위
DATA, MODELS, RESULTS = ROOT / "data", ROOT / "models", ROOT / "results"

ARTICLES = DATA / "labor_articles_20261001.csv"
EVAL = DATA / "labor_eval_keywords.csv"
EMB = MODELS / "emb_final_v3_fp16"
LLM_4BIT = MODELS / "sllm_mlx_4bit"
