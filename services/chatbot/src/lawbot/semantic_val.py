"""Deterministic holdouts and protection checks for semantic calibration."""

import re
from collections import Counter, defaultdict
from itertools import permutations
from pathlib import Path

from lawbot.cache_seed import csv_rows, json_lines
from lawbot.calc_gate import is_calculation_question
from lawbot.dataset_store import protected_questions
from lawbot.db.dataset_import import DATASETS
from lawbot.exact_cache import canonical, digest, normalize
from lawbot.qa_rules import THRESHOLD, Overlap, norm, protected_split
from lawbot.semantic_handoff import Handoff, Question, evidence_result


class Protection:
    def __init__(self, questions):
        normalized = sorted({norm(normalize(q)) for q in questions} - {''})
        self.fingerprint = digest(canonical(['semantic-protection-v1', THRESHOLD, [digest(q) for q in normalized]]))
        self.exact = set(normalized)
        buckets = defaultdict(list)
        for index, question in enumerate(normalized):
            buckets[len(question)].append(('protected', index, question))
        self.by_length = {length: Overlap(rows) for length, rows in buckets.items()}
        self.results: dict[str, str | None] = {}

    def match(self, question: str) -> str | None:
        key = norm(normalize(question))
        if key in self.exact:
            return 'exact'
        if key not in self.results:
            self.results[key] = (
                'near'
                if any(
                    index.match(key)
                    for length, index in self.by_length.items()
                    if 2 * min(len(key), length) >= THRESHOLD * (len(key) + length)
                )
                else None
            )
        return self.results[key]


def repo_protection(root: Path, corpus_db: Path | None = None) -> Protection:
    """Read frozen repository split questions solely for exclusion checks."""
    questions = []
    for dataset in DATASETS.values():
        accepted = None
        if dataset.split is not None:
            split_rows = csv_rows((root / dataset.split).read_bytes())
            accepted = {row['id'] for row in split_rows if protected_split(row['split_emb'], row['split_sllm'])}
        rows = csv_rows((root / dataset.path).read_bytes())
        protected = [(row['id'], row['question']) for row in rows if accepted is None or row['id'] in accepted]
        if not protected or (accepted is not None and {key for key, _ in protected} != accepted):
            raise ValueError('incomplete protected split')
        questions.extend(question for _, question in protected)
    if corpus_db is not None:
        questions.extend(protected_questions(corpus_db))
    return Protection(questions)


def source_query_id(source: str, source_id: str) -> str:
    return digest(canonical([source, source_id]))


def val_row(q: Question, source: str, target: str | None, label: str) -> dict:
    return {
        'source': source,
        'query_id': source_query_id(source, q.id),
        'target_answer_id': target,
        'label': label,
        'split_v4': 'train'
        if (q.metadata.get('split_emb'), q.metadata.get('split_sllm')) == ('train', 'train')
        else 'unknown',
        'v6_embedding_training': 'likely' if source == 'pass' else 'unverified',
    }


def build_val(seed: Handoff, deferred: tuple[Question, ...], protection: Protection, snapshot):
    answers = {answer.key: answer for answer in seed.answers}
    valid_keys = {
        key for key, answer in answers.items() if set(evidence_result(answer, snapshot).values()) == {'match'}
    }
    groups = defaultdict(list)
    exclusions: Counter[str] = Counter()
    rows = []
    for source, questions in (('pass', seed.questions), ('deferred', deferred)):
        for q in questions:
            if overlap := protection.match(q.text):
                exclusions[f'val_{source}_protected_{overlap}'] += 1
                continue
            if is_calculation_question(q.text):
                exclusions[f'val_{source}_calculation'] += 1
                continue
            if source == 'pass':
                if q.answer_key in valid_keys:
                    groups[q.answer_key].append(q)
            else:
                label = 'uncertain' if q.metadata['pair_verdict'] == 'review' else 'not_reusable'
                rows.append(val_row(q, source, None, label))
    representatives = {}
    by_article = defaultdict(list)
    held = []
    for key, variants in groups.items():
        question = min(variants, key=lambda q: (digest(normalize(q.text)), q.id))
        representatives[key] = question
        by_article[answers[key].parent].append(key)
        if len(variants) >= 2:
            rows.append(val_row(question, 'pass', answers[key].group_id, 'reusable'))
            held.append(question.id)
    for keys in by_article.values():
        for source_key, target_key in permutations(keys, 2):
            rows.append(val_row(representatives[source_key], 'pass', answers[target_key].group_id, 'not_reusable'))
    rows.sort(key=lambda row: (row['label'], row['query_id'], row['target_answer_id'] or ''))
    counts = Counter(row['label'] for row in rows)
    return rows, sorted(held), dict(exclusions | counts)


def read_holdout(path: Path) -> set[str]:
    rows = json_lines(path.read_bytes())
    selected = {row['query_id'] for row in rows if row.get('source') == 'pass' and row.get('label') == 'reusable'}
    if not selected or any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key) for key in selected):
        raise ValueError('invalid reusable val holdout IDs')
    return selected
