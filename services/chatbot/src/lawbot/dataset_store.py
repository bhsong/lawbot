"""Read imported QA for leakage checks and write promoted feedback datasets."""

import json
from dataclasses import dataclass
from pathlib import Path

from lawbot.db.connection import connect
from lawbot.db.dataset_import import DATASETS
from lawbot.qa_rules import norm, protected_split


@dataclass(frozen=True)
class ProtectionRow:
    qa_uid: str
    split_set: str
    source_id: str
    question: str
    split: str


def protection_rows(path: Path) -> list[ProtectionRow]:
    """Require the imported protected sets and classify each item once."""
    with connect(path) as conn:
        rows = conn.execute(
            'SELECT q.qa_uid,q.dataset,q.source_id,q.question,'
            "MAX(CASE s.purpose WHEN 'emb' THEN s.split END),"
            "MAX(CASE s.purpose WHEN 'sllm' THEN s.split END),"
            "MAX(CASE s.purpose WHEN 'eval' THEN s.split END) "
            'FROM qa_items q JOIN qa_splits s ON s.qa_uid=q.qa_uid AND s.split_set=q.dataset '
            'JOIN split_sets ss ON ss.split_set=s.split_set '
            "WHERE q.status='active' AND ss.frozen=1 GROUP BY q.qa_uid ORDER BY q.dataset,q.row_no,q.qa_uid"
        ).fetchall()
    items = []
    for uid, dataset, source, question, emb, sllm, evaluation in rows:
        spec = DATASETS.get(dataset)
        if spec is None:
            continue
        if spec.split is None and evaluation == 'test':
            split = protected_split(None, None, evaluation=True)
        elif spec.split is not None and emb is not None and sllm is not None:
            split = protected_split(emb, sllm) or 'train'
        else:
            continue
        if split is not None:
            items.append(ProtectionRow(uid, dataset, source, question, split))
    missing = sorted(DATASETS.keys() - {item.split_set for item in items if item.split != 'train'})
    if missing:
        raise RuntimeError(
            '관리자 API에 필요한 QA 세트가 없습니다. migrate_csv_to_sqlite qa --set …를 먼저 실행하세요. '
            + ', '.join(missing)
        )
    return items


def insert_feedback_qa(conn, *, key, feedback_id, question, answer, gold, rejected, at):
    """Write the QA, answer, unassigned splits, and optional preference together."""
    uid = f'feedback:{key}'
    law, article_no = gold[0]['law'], gold[0]['article_no']
    article_id = f'{law} {article_no}'
    normalized = norm(question)
    row = dict(id=key, question=question, law=law, article_id=article_id, answer_key=key)
    conn.execute(
        'INSERT INTO answer_keys (key_set,answer_key,law,article_id,answer,must_include,must_not_include,'
        "must_not_in_conclusion) VALUES ('feedback',?,?,?,?,'[]','[]','[]')",
        (key, law, article_id, answer),
    )
    conn.execute(
        'INSERT INTO qa_items (qa_uid,dataset,source_id,question,question_norm,law,article_id,key_set,answer_key,'
        "row,origin,status,created_at) VALUES (?,'feedback',?,?,?,?,?,'feedback',?,?,?,'active',?)",
        (
            uid,
            key,
            question,
            normalized,
            law,
            article_id,
            key,
            json.dumps(row, ensure_ascii=False),
            f'feedback:{feedback_id}',
            at,
        ),
    )
    conn.executemany(
        'INSERT INTO qa_gold_articles VALUES (?,?,?)',
        ((uid, article['law'], article['article_no']) for article in gold),
    )
    conn.execute("INSERT INTO split_sets VALUES ('feedback',0,'{}') ON CONFLICT DO NOTHING")
    conn.executemany(
        "INSERT INTO qa_splits VALUES (?,'feedback',?,'unassigned',?,NULL,?)",
        ((uid, purpose, normalized, at) for purpose in ('emb', 'sllm')),
    )
    preference = None
    if rejected is not None:
        preference = conn.execute(
            'INSERT INTO qa_preferences (qa_uid,chosen,rejected,rejected_feedback_id,verdict,tasks,created_at) '
            "VALUES (?,?,?,?,'incorrect',?,?)",
            (uid, answer, rejected, feedback_id, json.dumps(['dpo', 'kto']), at),
        ).lastrowid
    return uid, preference


def retire_feedback_qa(conn, uid):
    conn.execute("UPDATE qa_items SET status='retired' WHERE qa_uid=?", (uid,))


def feedback_assignments(conn, feedback_id):
    return [
        ProtectionRow(uid, 'feedback', source, question, split)
        for uid, source, question, split in conn.execute(
            'SELECT q.qa_uid,q.source_id,q.question,MAX(s.split) FROM qa_items q JOIN qa_splits s USING (qa_uid) '
            "WHERE q.dataset='feedback' AND s.split_set='feedback' AND s.split IN ('train','val') "
            "AND (q.origin<>? OR q.status='retired') "
            'GROUP BY q.qa_uid ORDER BY MAX(s.split) DESC,q.qa_uid',
            (f'feedback:{feedback_id}',),
        )
    ]
