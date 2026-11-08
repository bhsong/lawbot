"""Synthetic rows for administrator API and startup tests."""

import json

from lawbot.db.connection import connect, transaction
from lawbot.feedback_store import append
from lawbot.qa_rules import norm

SETS = ('v4', 'v5', 'decree_v4', 'reasoning_v5', 'oos', 'scope', 'refusal_hard', 'labor_eval')
TEST_QUESTION = '보호용문항철도차량점검일지'
VAL_QUESTION = '검증용문항항공정비기록원본'
EVAL_QUESTION = '평가전용문항고래관찰허가'
TRAIN_QUESTION = '훈련용합성문항우주정거장출입기록'
GOLD = [{'law': '근로기준법', 'article_no': '제26조'}]


def seed_qa(path):
    with connect(path, write=True) as conn, transaction(conn):
        for name in SETS:
            conn.execute("INSERT INTO split_sets VALUES (?,0,'{}')", (name,))
            rows = {
                'v5': [
                    (TEST_QUESTION, 'test', 'test'),
                    (VAL_QUESTION, 'train', 'val'),
                    (TRAIN_QUESTION, 'train', 'train'),
                ],
                'oos': [(EVAL_QUESTION, 'test', 'test')],
            }.get(name, [(f'{name} 합성보호자료', 'test', 'test')])
            for i, (question, emb, sllm) in enumerate(rows):
                uid = f'{name}:{i}'
                conn.execute(
                    'INSERT INTO qa_items (qa_uid,dataset,source_id,question,question_norm,row,origin,status,created_at) '
                    "VALUES (?,?,?,?,?,'{}','test','active','now')",
                    (uid, name, str(i), question, norm(question)),
                )
                splits = (('emb', emb), ('sllm', sllm)) if name in SETS[:3] else (('eval', 'test'),)
                conn.executemany(
                    "INSERT INTO qa_splits VALUES (?,?,?,?,?,NULL,'now')",
                    [(uid, name, purpose, split, norm(question)) for purpose, split in splits],
                )
            conn.execute('UPDATE split_sets SET frozen=1 WHERE split_set=?', (name,))


def add_feedback(path, fid, **fields):
    record = (
        dict(
            feedback_id=fid,
            turn_id=fid,
            ts='2026-11-08T00:00:00+0000',
            label='like',
            reason=None,
            question='해고 예고가 필요한가요?',
            answer_raw='답변 원문',
            answer_shown='화면 답변',
            model='pro',
            profile='v5',
            dataset_version='old',
            articles_sha256='old-hash',
        )
        | fields
    )
    with connect(path, write=True) as conn:
        append(conn, record)


def add_review(path, fid, **fields):
    row = dict(
        verdict='correct', decision='approved', gold_articles=GOLD, corrected_question=None, corrected_answer=None
    )
    row.update(fields)
    with connect(path, write=True) as conn:
        conn.execute(
            'INSERT INTO feedback_reviews (feedback_id, reviewer, verdict, decision, gold_articles, '
            "corrected_question, corrected_answer, created_at) VALUES (?,'reviewer',?,?,?,?,?,'now')",
            (
                fid,
                row['verdict'],
                row['decision'],
                json.dumps(row['gold_articles']),
                row['corrected_question'],
                row['corrected_answer'],
            ),
        )
