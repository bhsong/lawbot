import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lawbot.db.connection import transaction
from lawbot.db.csv_import import CsvFile, CsvLayout, read_csv, sha256, source_name
from lawbot.paths import DATA
from lawbot.qa_rules import norm

QA_COLUMNS = (
    'id',
    'question',
    'law',
    'article_id',
    'title',
    'question_type',
    'confusable_with',
    'eval_cat',
    'answer_key',
)
SPLIT_COLUMNS = ('id', 'eval_cat', 'split_emb', 'split_sllm')
KEY_COLUMNS = (
    'answer_key',
    'law',
    'article_id',
    'answer',
    'must_include',
    'must_not_include',
    'must_not_in_conclusion',
)
PURPOSES = ('emb', 'sllm')
SPLITS = ('train', 'val', 'test')


@dataclass(frozen=True)
class DatasetSpec:
    path: str
    columns: tuple[str, ...]
    rows: int
    split: str | None = None
    keys: str | None = None
    key_rows: int | None = None


DATASETS = {
    'v4': DatasetSpec('v4/qa_full_v4.csv', QA_COLUMNS, 7618, 'v4/split_v4.csv', 'v4/answer_key_v4.csv', 751),
    'v5': DatasetSpec(
        'v5/qa_tax_v5.csv', (*QA_COLUMNS, 'rid'), 13680, 'v5/split_tax_v5.csv', 'v5/answer_key_tax_v5.csv', 1745
    ),
    'decree_v4': DatasetSpec(
        'decree_exp/qa_decree_v4.csv', (*QA_COLUMNS, 'rid'), 4096, 'decree_exp/split_decree_v4.csv'
    ),
    'reasoning_v5': DatasetSpec(
        'v5/qa_reasoning_val.csv', ('id', 'error_type', 'question', 'answer', 'cited_articles', 'article_ids'), 30
    ),
    'oos': DatasetSpec(
        'oos_eval.csv', ('id', 'category', 'source', 'question', 'near_lsa', 'near_lsa_v2', 'refer_lsa', 'note'), 102
    ),
    'scope': DatasetSpec('scope_controls.csv', ('id', 'article_id', 'twin_oos', 'question'), 20),
    'refusal_hard': DatasetSpec(
        'refusal_hard_cases.csv',
        ('test', 'id', 'question', 'article_id', 'context_ids', 'cited', 'claim', 'label', 'reason'),
        86,
    ),
    'labor_eval': DatasetSpec(
        'labor_eval_keywords.csv',
        (
            'id',
            'question',
            'question_type',
            'article_id',
            'title',
            'confusable_with',
            'cite_pattern',
            'must_include',
            'must_not_include',
            'must_not_in_conclusion',
            'n_groups',
            'oracle_context_ids',
            'oracle_prompt',
            'reference_answer',
            'reference_len',
        ),
        650,
    ),
}


@dataclass(frozen=True)
class DatasetImport:
    name: str
    qa: CsvFile
    split: CsvFile | None
    keys: CsvFile | None
    unique_ids: bool

    @property
    def row_count(self) -> int:
        return len(self.qa.rows)

    def files(self) -> dict[str, CsvFile]:
        return {
            name: file
            for name, file in (('qa', self.qa), ('split', self.split), ('keys', self.keys))
            if file is not None
        }

    def uid(self, row: dict[str, str], position: int) -> str:
        return f'{self.name}:{row["id"]}' if self.unique_ids else f'{self.name}:#{position}'


def prepare_dataset(name: str, override: Path | None = None) -> DatasetImport:
    """Read all source files and validate relational references before any DB writes."""
    spec = DATASETS[name]
    qa = read_csv(override or DATA / spec.path, spec.columns, spec.rows)
    split = read_csv(DATA / spec.split, SPLIT_COLUMNS, spec.rows) if spec.split else None
    keys = read_csv(DATA / spec.keys, KEY_COLUMNS, spec.key_rows) if spec.keys else None
    files = [file.path.name for file in (qa, split, keys) if file is not None]
    if len(set(files)) != len(files):
        raise ValueError(f'{name}의 입력 CSV 파일 이름이 중복됩니다.')
    by_id = {row['id']: row for row in qa.rows}
    unique = len(by_id) == len(qa.rows)
    if '' in by_id or any(not row['question'] for row in qa.rows):
        raise ValueError(f'{name}에 비어 있는 문항 ID 또는 질문이 있습니다.')
    if split is not None:
        if not unique or {row['id'] for row in split.rows} != by_id.keys():
            raise ValueError(f'{name}의 문항·분할 ID가 일대일로 대응하지 않습니다.')
        for position, row in enumerate(split.rows, 1):
            if row['eval_cat'] != by_id[row['id']]['eval_cat'] or any(
                row[f'split_{purpose}'] not in SPLITS for purpose in PURPOSES
            ):
                raise ValueError(f'{name}의 분할 {position}행에 잘못된 분할 값 또는 범주가 있습니다.')
    if keys is not None:
        ids = {row['answer_key'] for row in keys.rows}
        if '' in ids or len(ids) != len(keys.rows):
            raise ValueError(f'{name}의 정답 키가 비어 있거나 중복됩니다.')
        if any(row['answer_key'] not in ids for row in qa.rows):
            raise ValueError(f'{name}에 정답 키 CSV에 없는 참조가 있습니다.')
    return DatasetImport(name, qa, split, keys, unique)


def _policy(conn: sqlite3.Connection, name: str) -> dict:
    row = conn.execute('SELECT frozen, policy FROM split_sets WHERE split_set=?', (name,)).fetchone()
    if row is None or row[0] != 1:
        raise ValueError(f'{name}의 동결된 분할 정책을 찾을 수 없습니다.')
    return json.loads(row[1])


def _split_records(conn: sqlite3.Connection, name: str):
    for sid, category, emb, sllm in conn.execute(
        'SELECT qi.source_id, qi.eval_cat, e.split, s.split FROM qa_splits AS e '
        'JOIN qa_splits AS s ON s.qa_uid=e.qa_uid AND s.split_set=e.split_set AND s.row_no=e.row_no '
        "JOIN qa_items AS qi ON qi.qa_uid=e.qa_uid WHERE e.split_set=? AND e.purpose='emb' AND s.purpose='sllm' "
        'ORDER BY e.row_no',
        (name,),
    ):
        yield dict(id=sid, eval_cat=category, split_emb=emb, split_sllm=sllm)


def dataset_bytes(conn: sqlite3.Connection, name: str) -> dict[str, bytes]:
    """Reconstruct original QA, split, and answer-key files in source order."""
    policy = _policy(conn, name)
    files = policy['files']
    records = {
        'qa': (
            json.loads(raw)
            for (raw,) in conn.execute('SELECT row FROM qa_items WHERE dataset=? ORDER BY row_no', (name,))
        )
    }
    if 'split' in files:
        records['split'] = _split_records(conn, name)
    if 'keys' in files:
        fields = ', '.join(KEY_COLUMNS)
        records['keys'] = (
            dict(zip(KEY_COLUMNS, row, strict=True))
            for row in conn.execute(f'SELECT {fields} FROM answer_keys WHERE key_set=? ORDER BY row_no', (name,))
        )
    restored = {}
    for role, meta in files.items():
        raw = CsvLayout(**meta['layout']).render(records[role])
        if sha256(raw) != meta['sha256']:
            raise ValueError(f'{name}의 {role} 복원 해시가 저장된 해시와 다릅니다.')
        restored[Path(meta['path']).name] = raw
    return restored


def dataset_sources(conn: sqlite3.Connection, name: str) -> dict[str, Path]:
    return {Path(meta['path']).name: Path(meta['path']) for meta in _policy(conn, name)['files'].values()}


def _write_items(conn: sqlite3.Connection, dataset: DatasetImport, now: str) -> dict[str, str]:
    name = dataset.name
    origin = 'import:' + source_name(dataset.qa.path)
    key_set = name if dataset.keys is not None else None
    normalized = {}
    for position, row in enumerate(dataset.qa.rows):
        uid = dataset.uid(row, position)
        group_key = norm(row['question'])
        normalized[uid] = group_key
        conn.execute(
            'INSERT INTO qa_items (qa_uid, dataset, source_id, row_no, question, question_norm, law, article_id, '
            'question_type, eval_cat, key_set, answer_key, row, origin, status, created_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (
                uid,
                name,
                row['id'],
                position,
                row['question'],
                group_key,
                row.get('law'),
                row.get('article_id'),
                row.get('question_type'),
                row.get('eval_cat'),
                key_set,
                row['answer_key'] if key_set else None,
                json.dumps(row, ensure_ascii=False),
                origin,
                now,
            ),
        )
    return normalized


def _split_values(dataset: DatasetImport, groups: dict[str, str], now: str):
    name = dataset.name
    if dataset.split is None:
        for position, (uid, group_key) in enumerate(groups.items()):
            yield uid, name, 'eval', 'test', group_key, position, now
        return
    for position, row in enumerate(dataset.split.rows):
        uid = dataset.uid(row, position)
        for purpose in PURPOSES:
            yield uid, name, purpose, row[f'split_{purpose}'], groups[uid], position, now


def write_dataset(conn: sqlite3.Connection, dataset: DatasetImport) -> int:
    """Write a complete dataset in one transaction and freeze its split."""
    name = dataset.name
    files = dataset.files()
    now = datetime.now(UTC).isoformat()
    policy = {
        'rule': 'source-split' if dataset.split else 'evaluation-only',
        'group_key': 'question_norm',
        'answer_key': 'foreign-key' if dataset.keys else 'source-row-only',
        'files': {role: file.metadata() for role, file in files.items()},
    }
    with transaction(conn):
        if conn.execute('SELECT 1 FROM split_sets WHERE split_set=?', (name,)).fetchone():
            if tuple(dataset_bytes(conn, name).values()) != tuple(
                file.layout.render(file.rows) for file in files.values()
            ):
                raise ValueError(f'같은 QA 세트 ID의 원본 내용이 다릅니다: {name}')
            return 0
        conn.execute('INSERT INTO split_sets VALUES (?,0,?)', (name, json.dumps(policy, ensure_ascii=False)))
        if dataset.keys is not None:
            fields = ', '.join(KEY_COLUMNS)
            conn.executemany(
                f'INSERT INTO answer_keys (key_set, {fields}, row_no) VALUES (?,?,?,?,?,?,?,?,?)',
                ((name, *(row[key] for key in KEY_COLUMNS), i) for i, row in enumerate(dataset.keys.rows)),
            )
        groups = _write_items(conn, dataset, now)
        conn.executemany('INSERT INTO qa_splits VALUES (?,?,?,?,?,?,?)', _split_values(dataset, groups, now))
        conn.execute('UPDATE split_sets SET frozen=1 WHERE split_set=?', (name,))
    return dataset.row_count
