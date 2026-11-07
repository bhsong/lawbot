"""Shared rules for reviewing feedback and detecting evaluation-set overlap."""

import difflib
import re

THRESHOLD = 0.9
LEAK_SPLITS = ('val', 'test')
OK_TO_VERDICT = {'맞음': 'correct', '틀림': 'incorrect', '모름': 'uncertain'}


def norm(q):
    """Keep word characters other than underscores and lowercase the result."""
    return re.sub(r'[\W_]+', '', str(q)).lower()


class Overlap:
    """Match normalized questions exactly first, then by sequence similarity."""

    def __init__(self, evals, threshold=THRESHOLD):
        self.threshold = threshold
        self.items = [(f'{src}:{i}', norm(q)) for src, i, q in evals]
        self.exact = {}
        for key, n in self.items:
            self.exact.setdefault(n, key)

    def match(self, question):
        """Return the matching source ID and 'exact' or a similarity score."""
        n = norm(question)
        if n in self.exact:
            return self.exact[n], 'exact'
        best = None
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(n)
        for key, e in self.items:
            sm.set_seq1(e)
            if sm.real_quick_ratio() < self.threshold or sm.quick_ratio() < self.threshold:
                continue
            r = sm.ratio()
            if r >= self.threshold and (best is None or r > best[1]):
                best = (key, r)
        return (best[0], round(best[1], 3)) if best else None


def latest_rows(rows):
    """Return the latest rows and cleared count, breaking ties by input order."""
    last = {}
    for r in sorted(rows, key=lambda r: r.get('ts') or ''):
        last[r['turn_id']] = r
    kept = [r for r in last.values() if r.get('label') != 'cleared']
    return kept, len(last) - len(kept)
