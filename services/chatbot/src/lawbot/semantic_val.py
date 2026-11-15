"""Deterministic holdouts and protection checks for semantic calibration."""

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import file_digest
from itertools import permutations
from pathlib import Path

from lawbot.cache_seed import Protection, json_lines
from lawbot.calc_gate import is_calculation_question
from lawbot.exact_cache import canonical, digest, normalize
from lawbot.qa_rules import norm
from lawbot.semantic_handoff import Handoff, Question, evidence_result

V6_SPLITS = {
    'labor': ('87c239f7006e', '49f495cff751'),
    'decree': ('432148e457cb', '70a9431d10b3'),
    'tax': ('2e5467ca8466', '3d91721ef33b'),
    'new': ('a9a34acc6a21', '8b69ee46ba76'),
}


@dataclass(frozen=True)
class TrainingExposure:
    by_id: dict[str, str]
    normalized: frozenset[str]

    def classify(self, question: Question) -> str:
        if norm(normalize(question.text)) in self.normalized:
            return 'same_text'
        return 'same_id_changed' if question.id in self.by_id else 'none'


def training_group(questions, splits) -> dict[str, str]:
    train = {row['id'] for row in splits if row['split_emb'] == 'train'}
    matched = {row['id']: norm(normalize(row['question'])) for row in questions if row['id'] in train}
    if matched.keys() != train or not train or not all(matched.values()):
        raise ValueError('incomplete v6 embedding training rows')
    return matched


def read_v6_training(root: Path) -> TrainingExposure:
    """Read only embedding-train questions from the recorded v6 Colab inputs."""
    by_id: dict[str, str] = {}
    for group, (qa_hash, split_hash) in V6_SPLITS.items():
        qa = root / f'qa_{group}_v6.csv'
        split = root / f'split_{group}_v6.csv'
        for path, expected in ((qa, qa_hash), (split, split_hash)):
            with path.open('rb') as stream:
                if not file_digest(stream, 'sha256').hexdigest().startswith(expected):
                    raise ValueError(f'v6 training input checksum mismatch: {path.name}')
        with (
            qa.open(encoding='utf-8-sig', newline='') as questions,
            split.open(encoding='utf-8-sig', newline='') as splits,
        ):
            group_rows = training_group(csv.DictReader(questions), csv.DictReader(splits))
        if by_id.keys() & group_rows.keys():
            raise ValueError('duplicate v6 embedding training IDs')
        by_id.update(group_rows)
    return TrainingExposure(by_id, frozenset(by_id.values()))


def source_query_id(source: str, source_id: str) -> str:
    return digest(canonical([source, source_id]))


def val_row(q: Question, source: str, target: str | None, label: str, training: TrainingExposure | None) -> dict:
    return {
        'source': source,
        'query_id': source_query_id(source, q.id),
        'target_answer_id': target,
        'label': label,
        'split_v4': 'train'
        if (q.metadata.get('split_emb'), q.metadata.get('split_sllm')) == ('train', 'train')
        else 'unknown',
        'v6_embedding_training': training.classify(q) if training else ('likely' if source == 'pass' else 'unverified'),
    }


def build_val(seed: Handoff, deferred: tuple[Question, ...], protection: Protection, snapshot, *, training=None):
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
                rows.append(val_row(q, source, None, label, training))
    representatives = {}
    by_article = defaultdict(list)
    held = []
    for key, variants in groups.items():
        question = min(variants, key=lambda q: (digest(normalize(q.text)), q.id))
        representatives[key] = question
        by_article[answers[key].parent].append(key)
        if len(variants) >= 2:
            rows.append(val_row(question, 'pass', answers[key].group_id, 'reusable', training))
            held.append(question.id)
    for keys in by_article.values():
        for source_key, target_key in permutations(keys, 2):
            rows.append(
                val_row(representatives[source_key], 'pass', answers[target_key].group_id, 'not_reusable', training)
            )
    rows.sort(key=lambda row: (row['label'], row['query_id'], row['target_answer_id'] or ''))
    counts = Counter(row['label'] for row in rows)
    return rows, sorted(held), dict(exclusions | counts)


def read_holdout(path: Path) -> set[str]:
    rows = json_lines(path.read_bytes())
    selected = {row['query_id'] for row in rows if row.get('source') == 'pass' and row.get('label') == 'reusable'}
    if not selected or any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key) for key in selected):
        raise ValueError('invalid reusable val holdout IDs')
    return selected
