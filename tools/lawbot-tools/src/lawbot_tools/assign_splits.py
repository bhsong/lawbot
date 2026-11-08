"""Assign feedback train/val splits before running export_dataset_snapshot."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from typing import NamedTuple

from lawbot.db.connection import connect, repo_path, transaction
from lawbot.db.dataset_import import PURPOSES
from lawbot.db.importers import DatabaseError
from lawbot.db.migrate import check_schema
from lawbot.qa_rules import THRESHOLD, Overlap

ASSIGNED = ('train', 'val')


class SplitRow(NamedTuple):
    uid: str
    question: str
    status: str
    purpose: str
    split: str
    group_key: str


def _state(conn, split_set):
    policy = conn.execute('SELECT frozen, policy FROM split_sets WHERE split_set=?', (split_set,)).fetchone()
    rows = conn.execute(
        'SELECT q.qa_uid,q.question,q.status,s.purpose,s.split,s.group_key '
        'FROM qa_items q JOIN qa_splits s USING (qa_uid) '
        "WHERE q.dataset='feedback' AND s.split_set=? AND s.purpose IN ('emb','sllm') ORDER BY q.qa_uid,s.purpose",
        (split_set,),
    )
    return policy, [SplitRow(*row) for row in rows]


def _train_index(conn):
    return Overlap(
        conn.execute(
            'SELECT DISTINCT q.dataset,q.qa_uid,q.question FROM qa_items q JOIN qa_splits s USING (qa_uid) '
            "WHERE q.dataset IN ('v4','v5') AND q.status='active' AND s.split_set=q.dataset AND s.split='train'"
        )
    )


def _groups(questions):
    parent = {uid: uid for uid in questions}

    def root(uid):
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    earlier = []
    for uid, question in questions.items():
        index = Overlap([('feedback', uid, question)])
        for candidate, candidate_index in earlier:
            # A best match against the whole pool would miss bridges between groups.
            if root(candidate) != root(uid) and (candidate_index.match(question) or index.match(questions[candidate])):
                parent[root(uid)] = root(candidate)
        earlier.append((uid, index))

    groups = {}
    for uid in questions:
        groups.setdefault(root(uid), []).append(uid)
    return list(groups.values())


def _choose_split(seed, key, val_frac):
    value = int.from_bytes(hashlib.sha256(f'{seed}:{key}'.encode()).digest())
    numerator, denominator = val_frac.as_integer_ratio()
    return 'val' if value * denominator < numerator * (1 << 256) else 'train'


def _plan(rows, groups, fractions, seed):
    by_purpose = {(row.uid, row.purpose): row for row in rows}
    updates, conflicts = [], []
    results = {purpose: dict(assigned=0, inherited=0, conflicts=0) for purpose in PURPOSES}
    for group in groups:
        for purpose, frac in fractions.items():
            members = [by_purpose[uid, purpose] for uid in group if (uid, purpose) in by_purpose]
            pending = [row.uid for row in members if row.status == 'active' and row.split == 'unassigned']
            existing = {(row.split, row.group_key) for row in members if row.split in ASSIGNED}
            counts = results[purpose]
            if len({split for split, _ in existing}) > 1:
                counts['conflicts'] += 1
                conflicts.append(f'{purpose} 분할 충돌 · 그룹 {min(group)} · 미배정 {", ".join(pending) or "없음"}')
                continue
            if existing:
                split, key = min(existing)
                counts['inherited'] += len(pending)
            else:
                key = min(group)
                split = _choose_split(seed, key, frac)
                counts['assigned'] += len(pending)
            updates.extend((split, key, uid, purpose) for uid in pending)
    return updates, conflicts, results


def assign(db, split_set, fractions, seed):
    """Plan outside the write transaction, then commit only if its inputs still match."""
    check_schema(db)
    with connect(db) as conn:
        conn.execute('BEGIN')
        state = _state(conn, split_set)
        train = _train_index(conn)

    setting, rows = state
    if setting is not None and setting[0]:
        raise ValueError('고정 분할 세트는 배정할 수 없습니다.')
    if any(row.split == 'test' for row in rows):
        raise ValueError('피드백 분할 세트에 test 배정이 있습니다.')
    policy = json.loads(setting[1]) if setting is not None else {}
    if any(row.split in ASSIGNED for row in rows) and (
        policy.get('seed', seed) != seed or policy.get('val_frac', fractions) != fractions
    ):
        raise ValueError('이미 배정된 피드백이 있습니다. 기록된 분할 정책의 시드와 목적별 비율을 사용하세요.')
    questions = {row.uid: row.question for row in rows if row.status == 'active' or row.split in ASSIGNED}
    groups = _groups(questions)
    updates, conflicts, results = _plan(rows, groups, fractions, seed)
    overlap_count = sum(any(train.match(questions[uid]) for uid in group) for group in groups)

    with connect(db, write=True) as conn, transaction(conn):
        if _state(conn, split_set) != state:
            raise RuntimeError('배정 계산 중 피드백 분할 상태가 바뀌었습니다. 다시 실행하세요.')
        if setting is not None:
            at = datetime.now(UTC).isoformat()
            conn.executemany(
                'UPDATE qa_splits SET split=?,group_key=?,assigned_at=? '
                "WHERE qa_uid=? AND purpose=? AND split_set=? AND split='unassigned'",
                ((split, key, at, uid, purpose, split_set) for split, key, uid, purpose in updates),
            )
            if updates or conflicts or 'results' not in policy:
                policy.update(
                    rule='sha256(seed:group_key)',
                    threshold=THRESHOLD,
                    seed=seed,
                    val_frac=fractions,
                    groups=len(groups),
                    train_overlap_groups=overlap_count,
                    results=results,
                )
                conn.execute(
                    'UPDATE split_sets SET policy=? WHERE split_set=?',
                    (json.dumps(policy, ensure_ascii=False), split_set),
                )
    for purpose, counts in results.items():
        print(
            f'{purpose} · 신규 배정 {counts["assigned"]} · 상속 {counts["inherited"]} · 충돌 그룹 {counts["conflicts"]}'
        )
    print(f'그룹 {len(groups)} · 기존 v4/v5 train 중복 그룹 {overlap_count}')
    for conflict in conflicts:
        print(conflict)
    return int(bool(conflicts))


def _fraction(text):
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError('0 이상 1 이하의 비율을 지정하세요.')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='스냅샷을 내보내기 전에 피드백의 train/val 분할을 배정합니다.')
    parser.add_argument('--db', required=True)
    parser.add_argument('--split-set', choices=('feedback',), default='feedback')
    parser.add_argument('--val-frac', type=_fraction, default=0.1)
    parser.add_argument('--emb-val-frac', type=_fraction, help='임베딩 전용 val 비율')
    parser.add_argument('--sllm-val-frac', type=_fraction, help='sLLM 전용 val 비율')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    fractions = {'emb': args.emb_val_frac, 'sllm': args.sllm_val_frac}
    fractions = {purpose: args.val_frac if frac is None else frac for purpose, frac in fractions.items()}
    try:
        return assign(repo_path(args.db), args.split_set, fractions, args.seed)
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        print(f'FAIL {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
