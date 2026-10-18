import pandas as pd
from lawbot.paths import EVAL, RESULTS
from lawbot.retriever import Retriever

o = pd.read_csv(RESULTS / "scored_sllm_mlx_4bit.csv", encoding="utf-8-sig")
dev = pd.read_csv(EVAL, encoding="utf-8-sig").set_index("id")
R = Retriever()
for name, part in [
    ("틀림", o[o["retrieved"] & (o["cite_ok"] == 0)]),
    ("맞음", o[o["retrieved"] & (o["cite_ok"] == 1)]),
]:
    pos = []
    for i in part["id"]:
        ids = [h["article_id"] for h in R.search(dev.loc[i, "question"], k=3)]
        pos.append(ids.index(dev.loc[i, "article_id"]) + 1)
    print(
        name,
        len(pos),
        "문항, 정답 위치 분포:",
        pd.Series(pos).value_counts().sort_index().to_dict(),
    )
