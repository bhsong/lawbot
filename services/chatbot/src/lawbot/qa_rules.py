"""Shared rules for reviewing feedback and detecting evaluation-set overlap."""

import difflib
import re
from functools import lru_cache

THRESHOLD = 0.9
LEAK_SPLITS = ('val', 'test')
OK_TO_VERDICT = {'맞음': 'correct', '틀림': 'incorrect', '모름': 'uncertain'}


def norm(q):
    """Keep word characters other than underscores and lowercase the result."""
    return re.sub(r'[\W_]+', '', str(q)).lower()


def _exact(items):
    index = {}
    for key, question in items:
        index.setdefault(question, key)
    return index


def _match(normalized, items, exact, threshold):
    if normalized in exact:
        return exact[normalized], 'exact'
    best = None
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(normalized)
    for key, candidate in items:
        sm.set_seq1(candidate)
        if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
            continue
        score = sm.ratio()
        if score >= threshold and (best is None or score > best[1]):
            best = key, score
    return (best[0], round(best[1], 3)) if best else None


class Overlap:
    """Match normalized questions exactly first, then by sequence similarity."""

    def __init__(self, evals, threshold=THRESHOLD):
        self.threshold = threshold
        self.items = [(f'{src}:{i}', norm(q)) for src, i, q in evals]
        self.exact = _exact(self.items)

    def match(self, question):
        """Return the matching source ID and 'exact' or a similarity score."""
        return _match(norm(question), self.items, self.exact, self.threshold)


def latest_rows(rows):
    """Return the latest rows and cleared count, breaking ties by input order."""
    last = {}
    for r in sorted(rows, key=lambda r: r.get('ts') or ''):
        last[r['turn_id']] = r
    kept = [r for r in last.values() if r.get('label') != 'cleared']
    return kept, len(last) - len(kept)


def protected_split(emb, sllm, *, evaluation=False):
    """Classify both split purposes with test taking precedence over validation."""
    if evaluation:
        return 'eval'
    if 'test' in (emb, sllm):
        return 'test'
    if 'val' in (emb, sllm):
        return 'val'
    return None


class ProtectedQA:
    """Keep one read-only leakage index for the web app's lifetime."""

    def __init__(self, rows):
        protected = sorted(
            (row for row in rows if row.split != 'train'),
            key=lambda row: ('test', 'val', 'eval').index(row.split),
        )
        self.by_key = {f'{row.split}:{row.qa_uid}': row for row in protected}
        self.index = Overlap((row.split, row.qa_uid, row.question) for row in protected)
        self.train = {row.qa_uid: row for row in rows if row.split == 'train'}
        self.train_items = [(uid, norm(row.question)) for uid, row in self.train.items()]
        self.train_exact = _exact(self.train_items)
        self.match_question = lru_cache(maxsize=2048)(self._match_question)

    def _match_question(self, question):
        match = self.index.match(question)
        if match is not None:
            key, how = match
            row = self.by_key[key]
        else:
            match = _match(norm(question), self.train_items, self.train_exact, THRESHOLD)
            if match is None:
                return None
            key, how = match
            row = self.train[key]
        return dict(qa_uid=row.qa_uid, split_set=row.split_set, split=row.split, how=how)

    def match(self, *questions):
        matches = [match for q in questions if q and (match := self.match_question(q))]
        return min(matches, key=lambda row: ('test', 'val', 'eval', 'train').index(row['split']), default=None)


def assigned_overlap(rows, *questions):
    """Find a nonblocking overlap with an already assigned feedback question."""
    lookup = {row.qa_uid: row for row in rows}
    items = [(uid, norm(row.question)) for uid, row in lookup.items()]
    exact = _exact(items)
    for question in questions:
        if question and (match := _match(norm(question), items, exact, THRESHOLD)):
            key, how = match
            row = lookup[key]
            return dict(qa_uid=row.qa_uid, split_set=row.split_set, split=row.split, how=how)
    return None
